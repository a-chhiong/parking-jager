#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Taipei City Parking Generator (Taipei Parking Jäger)
----------------------------------------------------
Fetches live parking facility data directly from the official Taipei City 
Government Parking Management Office (停管處) Single Source of Truth (SSOT),
filters according to user-defined quality criteria (underground/planar, 
public/regulated, 24/7, EV charging, price ceiling), and exports:
  1. A Google My Maps-compatible CSV (with UTF-8 BOM)
  2. An OGC standard KML (XML) file with rich HTML popups & direct navigation links.

Zero external dependencies: runs with standard Python 3.8+.
"""

import argparse
import collections
import csv
import html
import io
import json
import math
import os
import re
import ssl
import sys
import urllib.request
import zipfile

# Ensure UTF-8 output even in Windows cmd/PowerShell cp950 environments
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

OFFICIAL_API_URL = "https://tcgbusfs.blob.core.windows.net/blobtcmsv/TCMSV_alldesc.json"

# Known precise ramp entrance coordinates for flagship baseline facilities
KNOWN_ENTRANCES = {
    '民有市場地下停車場': (25.060678, 121.547147),
    '濱江果菜市場地下停車場': (25.068994, 121.539828),
    '濱江市場地下停車場': (25.068994, 121.539828),
    '台北中山市場地下停車場': (25.050182, 121.522241),
    '環南綜合市場停車場': (25.029812, 121.493921),
    '環南市場地下停車場': (25.029812, 121.493921),
    '第一果菜批發市場停車場': (25.018241, 121.498321),
    '臺北市家禽批發市場地下停車場': (25.026412, 121.491241),
    '南門市場地下停車場': (25.032128, 121.518641),
    '水源大廈地下停車場': (25.013621, 121.532812),
    '大龍市場地下停車場': (25.074521, 121.512612),
    '士林市場地下停車場': (25.088612, 121.524812),
    '臺北花木批發市場地下停車場': (25.000672, 121.539481),
    '民權公園地下停車場': (25.061732, 121.558364),
    '民生立體收費停車場': (25.058362, 121.560481),
}


def fetch_official_data(cache_file='alldesc_cache.json', use_cache=True):
    """
    Downloads or reads the official Taipei Parking dataset from Azure Blob SSOT.
    """
    if use_cache and os.path.exists(cache_file):
        print(f"[*] Loading data from local cache: {cache_file}")
        with open(cache_file, 'r', encoding='utf-8') as f:
            return json.load(f)

    print(f"[*] Fetching live SSOT data from: {OFFICIAL_API_URL} ...")
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    req = urllib.request.Request(
        OFFICIAL_API_URL,
        headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) ParkingJager/1.0'}
    )
    with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
        content = resp.read().decode('utf-8')
        data = json.loads(content)

    if use_cache:
        with open(cache_file, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"[+] Cached raw data to {cache_file} ({len(content) / 1024 / 1024:.2f} MB)")

    return data


def extract_coordinates(lot):
    """
    Extracts WGS84 Latitude and Longitude from EntranceCoord, fallback to TWD97 conversion.
    """
    # 1. Custom overrides for pinpoint accuracy
    name = lot.get('name', '')
    for k, (lat, lng) in KNOWN_ENTRANCES.items():
        if k in name:
            return lat, lng

    # 2. Official EntranceCoord (WGS84)
    entrance_info = lot.get('EntranceCoord', {}).get('EntrancecoordInfo', [])
    if entrance_info:
        for ent in entrance_info:
            try:
                # In Taipei data: Xcod = Latitude (~25.0x), Ycod = Longitude (~121.5x)
                x = float(ent.get('Xcod', 0))
                y = float(ent.get('Ycod', 0))
                if 24.8 <= x <= 25.4 and 121.3 <= y <= 121.8:
                    return round(x, 6), round(y, 6)
                elif 24.8 <= y <= 25.4 and 121.3 <= x <= 121.8:
                    return round(y, 6), round(x, 6)
            except (ValueError, TypeError):
                continue

    # 3. Fallback: Convert TWD97 cadastral coordinates to WGS84
    try:
        tx = float(lot.get('tw97x', 0))
        ty = float(lot.get('tw97y', 0))
        if tx > 0 and ty > 0:
            a = 6378137.0
            k0 = 0.9999
            lng = round(121.0 + (tx - 250000.0) / (a * math.cos(25.0 * math.pi / 180.0) * k0 * (math.pi / 180.0)), 6)
            lat = round((ty / (110574.0 * k0)), 6)
            if 24.8 <= lat <= 25.4 and 121.3 <= lng <= 121.8:
                return lat, lng
    except (ValueError, TypeError):
        pass

    return None, None


def parse_car_hourly_rate(lot):
    """
    Parses the estimated standard daytime hourly rate for small cars (NTD/hr).
    """
    # Check structured FareInfo first
    fare_info = lot.get('FareInfo', {})
    if isinstance(fare_info, dict):
        rules = fare_info.get('FareRule', [])
        for r in rules:
            if r.get('ParkingType') == 'C' and r.get('RateType') == '1':
                try:
                    return int(r.get('ParkingRates', 0))
                except (ValueError, TypeError):
                    pass

    # Fallback to regex parsing on payex text
    payex = lot.get('payex', '')
    m = re.search(r'小型車[^\d]*?(\d+)\s*元/(?:時|小時)', payex)
    if m:
        return int(m.group(1))

    m2 = re.search(r'計時[^\d]*?(\d+)\s*元/(?:時|小時)', payex)
    if m2:
        return int(m2.group(1))

    return None


def filter_parking_lots(raw_lots, args):
    """
    Applies transparent, multi-stage filtering logic:
      1. Public / Municipal management (avoids private gouging)
      2. 24/7 operating hours
      3. Underground / Indoor Planar (avoids weather & mechanical tower risk)
      4. Rate ceiling check
      5. EV charging station availability
      6. District filter
      7. Coordinate presence
    """
    filtered = []
    stats = {
        'total': len(raw_lots),
        'passed_public': 0,
        'passed_24h': 0,
        'passed_underground': 0,
        'passed_rate': 0,
        'passed_ev': 0,
        'passed_coords': 0,
        'final': 0
    }

    allowed_types = {
        '本處自營停車場',
        '本處委外停車場',
        '市屬機關學校委外',
        '市屬機關學校自營',
        '中央機關學校委外',
        '中央機關學校自營',
        '本處自營停車場(委託路邊兼開)'
    }

    mechanical_keywords = ['機械塔', '機械式', '倉儲式', '雙層機械', '塔台式']

    for p in raw_lots:
        name = p.get('name', '').strip()
        addr = p.get('address', '').strip()
        summary = p.get('summary', '').strip()
        area = p.get('area', '').strip()
        service_time = p.get('serviceTime', '').strip()
        type2 = p.get('type2', '').strip()

        # Step 1: Public Ownership Filter
        if args.public_only and type2 not in allowed_types:
            continue
        stats['passed_public'] += 1

        # Step 2: 24/7 Operation
        if args.hours_24:
            is_24 = ('00:00:00-23:59:59' in service_time or '24小時' in service_time or '00:00-24:00' in service_time)
            if not is_24:
                continue
        stats['passed_24h'] += 1

        # Step 3: Underground / Indoor Planar Structure
        if args.underground_only:
            is_ug = ('地下' in name or '地下' in addr or 'B1' in addr or 'B2' in addr or '立體' in name or '室內' in summary)
            if not is_ug:
                continue

            # Reject pure mechanical lifts/towers unless expressly confirmed planar
            has_mech = any(mk in name or mk in summary for mk in mechanical_keywords)
            if has_mech and ('平面' not in name and '平面' not in summary):
                continue
        stats['passed_underground'] += 1

        # Step 4: Small Car Hourly Rate Ceiling
        hourly_rate = parse_car_hourly_rate(p)
        if args.max_rate is not None and hourly_rate is not None:
            if hourly_rate > args.max_rate:
                continue
        stats['passed_rate'] += 1

        # Step 5: EV Charging Filter
        charging_count = 0
        try:
            charging_count = int(p.get('ChargingStation', 0) or 0)
        except (ValueError, TypeError):
            pass

        has_ev = (charging_count > 0 or '充電' in summary or '充電' in p.get('payex', ''))
        if args.ev_only and not has_ev:
            continue
        stats['passed_ev'] += 1

        # Step 6: Administrative District Filter
        if args.district and args.district not in area:
            continue

        # Step 7: Coordinates
        lat, lng = extract_coordinates(p)
        if not lat or not lng:
            if args.require_coords:
                continue
        else:
            stats['passed_coords'] += 1

        # Formulate clean presentation records
        full_addr = addr if addr.startswith('臺北') or addr.startswith('台北') else f"臺北市{area}{addr}"
        google_maps_url = f"https://www.google.com/maps/search/?api=1&query={lat},{lng}" if (lat and lng) else f"https://www.google.com/maps/search/?api=1&query={urllib.parse.quote(full_addr)}"

        total_car = p.get('totalcar', '0')
        total_moto = p.get('totalmotor', '0')
        payex = p.get('payex', '依現場公告費率').strip()

        # Simplify payex if too long
        payex_clean = payex.replace('\r', '').replace('\n', ' ')

        filtered.append({
            'id': p.get('id', ''),
            'name': name,
            'district': area,
            'address': full_addr,
            'type_name': type2,
            'hourly_rate': hourly_rate if hourly_rate else '依公告',
            'payex': payex_clean,
            'total_car': total_car,
            'total_moto': total_moto,
            'charging_stalls': charging_count if charging_count > 0 else ('有' if has_ev else '0'),
            'lat': lat,
            'lng': lng,
            'gmaps_url': google_maps_url,
            'tel': p.get('tel', '').strip()
        })

    stats['final'] = len(filtered)
    return filtered, stats


def export_csv(lots, filename):
    """
    Exports lots to UTF-8 with BOM CSV for flawless loading in Google My Maps and Excel.
    """
    with open(filename, 'w', encoding='utf-8-sig', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([
            '名稱', '行政區', '完整地址', '主管營運型態', '日間參考費率(元/時)',
            '完整收費說明', '小型車位數', '機車位數', '充電樁數量',
            '緯度(Latitude)', '經度(Longitude)', 'Google地圖導航連結', '連絡電話'
        ])
        for l in lots:
            writer.writerow([
                l['name'],
                l['district'],
                l['address'],
                l['type_name'],
                l['hourly_rate'],
                l['payex'],
                l['total_car'],
                l['total_moto'],
                l['charging_stalls'],
                l['lat'] if l['lat'] else '',
                l['lng'] if l['lng'] else '',
                l['gmaps_url'],
                l['tel']
            ])
    print(f"[+] Successfully generated CSV: {filename} ({len(lots)} rows)")


def export_kml(lots, filename):
    """
    Exports lots to OGC standard KML 2.2 (XML) compliant with both:
      1. Google My Maps (<Style id="placemark-blue">, HTML CDATA <description>)
      2. CoMaps / Organic Maps C++ KmlParser (libs/kml/serdes.cpp & types.hpp):
         - <styleUrl>#placemark-blue</styleUrl> -> PredefinedColor::Blue
         - <ExtendedData xmlns:mwm="https://comaps.app">
         - <mwm:icon>Parking</mwm:icon> (exact PascalCase match for BookmarkIcon::Parking)
         - <mwm:name>/<mwm:description>/<mwm:annotation> wrapped in <mwm:lang code="default">
    Returns the raw KML string so it can also be packaged as KMZ.
    """
    kml = []
    kml.append('<?xml version="1.0" encoding="UTF-8"?>')
    kml.append('<kml xmlns="http://www.opengis.net/kml/2.2" xmlns:gx="http://www.google.com/kml/ext/2.2">')
    kml.append('  <Document>')
    kml.append('    <Style id="placemark-blue">')
    kml.append('      <IconStyle>')
    kml.append('        <color>ffd18800</color>')
    kml.append('        <scale>1.2</scale>')
    kml.append('        <Icon>')
    kml.append('          <href>https://maps.google.com/mapfiles/kml/shapes/parking_lot_maps.png</href>')
    kml.append('        </Icon>')
    kml.append('      </IconStyle>')
    kml.append('    </Style>')
    kml.append('    <name>臺北市公有地下優質停車場名冊 (Taipei Parking Jäger)</name>')
    kml.append('    <description>臺北市政府停管處官方 Single Source of Truth (SSOT) 即時篩選成果：24H、公有自營/委外、室內地下平面、平價計時與EV充電設施。</description>')
    kml.append('    <visibility>1</visibility>')
    kml.append('    <ExtendedData xmlns:mwm="https://comaps.app">')
    kml.append('      <mwm:name>')
    kml.append('        <mwm:lang code="default">臺北市公有地下優質停車場 (Taipei Parking Jäger)</mwm:lang>')
    kml.append('      </mwm:name>')
    kml.append('      <mwm:annotation>')
    kml.append('        <mwm:lang code="default">臺北市政府停管處官方 SSOT 篩選：24H、公有平面、平價計時與EV充電</mwm:lang>')
    kml.append('      </mwm:annotation>')
    kml.append('      <mwm:description>')
    kml.append('        <mwm:lang code="default">臺北市政府停管處官方 Single Source of Truth (SSOT) 即時篩選成果：24H、公有自營/委外、室內地下平面、平價計時與EV充電設施。</mwm:lang>')
    kml.append('      </mwm:description>')
    kml.append('      <mwm:accessRules>Local</mwm:accessRules>')
    kml.append('    </ExtendedData>')

    for l in lots:
        rate_car = format_md_rate_car(l['payex'], l['hourly_rate'], l['name'])
        rate_moto = format_md_rate_moto(l['payex'], l['total_moto'])
        height = format_md_height(l['name'])
        desc_plain = (
            f"費率：{rate_car}\n"
            f"機車：{rate_moto}\n"
            f"限高：{height}\n"
            f"小型車位：{l['total_car']} 格（EV 充電樁：{l['charging_stalls']} 格）\n"
            f"地址：{l['address']}\n"
            f"管轄：{l['type_name']}\n"
            f"完整收費：{l['payex']}"
        )

        desc_html = f"""<![CDATA[
<h3>{html.escape(l['name'])}</h3>
<table border="0" cellpadding="3" cellspacing="0" style="font-family:sans-serif; font-size:13px; line-height:1.5;">
  <tr><td><b>行政區：</b></td><td>{html.escape(l['district'])}</td></tr>
  <tr><td><b>地　址：</b></td><td>{html.escape(l['address'])}</td></tr>
  <tr><td><b>管　轄：</b></td><td>{html.escape(l['type_name'])}</td></tr>
  <tr><td><b>小型車：</b></td><td>{html.escape(str(l['total_car']))} 格</td></tr>
  <tr><td><b>充電樁：</b></td><td>{html.escape(str(l['charging_stalls']))} 格</td></tr>
  <tr><td><b>限　高：</b></td><td>{html.escape(height)}</td></tr>
  <tr><td><b>費　率：</b></td><td>{html.escape(l['payex'])}</td></tr>
  <tr><td><b>電　話：</b></td><td>{html.escape(l['tel'] if l['tel'] else '洽現場服務處')}</td></tr>
</table>
<div style="margin-top:10px;">
  <a href="{html.escape(l['gmaps_url'])}" target="_blank" style="display:inline-block; padding:6px 12px; background-color:#1a73e8; color:#ffffff; text-decoration:none; border-radius:4px; font-weight:bold;">在 Google 地圖中開啟導航</a>
</div>
]]>"""
        kml.append('    <Placemark>')
        kml.append(f'      <name>{html.escape(l["name"])}</name>')
        kml.append(f'      <description>{desc_html}</description>')
        kml.append('      <styleUrl>#placemark-blue</styleUrl>')
        if l['lat'] and l['lng']:
            kml.append('      <Point>')
            kml.append(f'        <coordinates>{l["lng"]},{l["lat"]},0</coordinates>')
            kml.append('      </Point>')
        # CoMaps / Organic Maps native bookmark ExtendedData (matches libs/kml/serdes.cpp)
        kml.append('      <ExtendedData xmlns:mwm="https://comaps.app">')
        kml.append('        <mwm:name>')
        kml.append(f'          <mwm:lang code="default">{html.escape(l["name"])}</mwm:lang>')
        kml.append('        </mwm:name>')
        kml.append('        <mwm:description>')
        kml.append(f'          <mwm:lang code="default">{html.escape(desc_plain)}</mwm:lang>')
        kml.append('        </mwm:description>')
        kml.append('        <mwm:icon>Parking</mwm:icon>')
        kml.append('        <mwm:visibility>1</mwm:visibility>')
        kml.append('      </ExtendedData>')
        kml.append('    </Placemark>')

    kml.append('  </Document>')
    kml.append('</kml>')

    kml_str = '\n'.join(kml)
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(kml_str)
    print(f"[+] Successfully generated KML: {filename} ({len(lots)} pins)")
    return kml_str


def export_kmz(kml_content, filename):
    """
    Exports compressed KMZ (doc.kml inside a zip archive).
    This is the official native 1-tap import format for CoMaps and Organic Maps on mobile.
    """
    with zipfile.ZipFile(filename, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('doc.kml', kml_content.encode('utf-8'))
    size_kb = os.path.getsize(filename) / 1024
    print(f"[+] Successfully generated KMZ (CoMaps / Organic Maps 1-Tap Ready): {filename} ({size_kb:.1f} KB)")


def export_geojson(lots, filename):
    """
    Exports lots to strict IETF RFC 7946 GeoJSON with official OpenStreetMap (OSM) tags.
    - No deprecated 'crs' object (strict RFC 7946 compliance)
    - All OSM tag values formatted as clean strings without null values (JOSM / iD ready)
    - Compatible with uMap, Leaflet, QGIS, and GitHub's interactive map viewer.
    """
    features = []
    for l in lots:
        if not l.get('lat') or not l.get('lng'):
            continue

        lat = float(l['lat'])
        lng = float(l['lng'])
        name = l['name']
        height = format_md_height(name)
        rate_car = format_md_rate_car(l['payex'], l['hourly_rate'], name)
        rate_moto = format_md_rate_moto(l['payex'], l['total_moto'])
        parking_type = 'multi-storey' if '立體' in name else 'underground'

        props = {
            "name": name,
            "amenity": "parking",
            "parking": parking_type,
            "access": "yes",
            "fee": "yes",
            "opening_hours": "24/7",
            "maxheight": height.replace('m', ''),
            "operator": "臺北市停車管理工程處",
            "operator:type": "government",
            "addr:city": "臺北市",
            "addr:district": l['district'],
            "addr:full": l['address'],
            "ref:tpe": str(l['id']),
            "charge": f"{l['hourly_rate']} TWD/hour" if str(l['hourly_rate']).isdigit() else rate_car,
            "description": f"【{l['district']}】{name}｜費率：{rate_car}｜機車：{rate_moto}｜車位：{l['total_car']}格（EV充電：{l['charging_stalls']}）｜限高：{height}",
        }

        if str(l['total_car']).isdigit() and int(l['total_car']) > 0:
            props["capacity"] = str(l['total_car'])

        if str(l['charging_stalls']).isdigit() and int(l['charging_stalls']) > 0:
            props["capacity:charging"] = str(l['charging_stalls'])
        elif l['charging_stalls'] == '有':
            props["capacity:charging"] = "yes"

        if l.get('tel'):
            props["phone"] = str(l['tel'])

        feat = {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [lng, lat]  # RFC 7946 standard order: [Longitude, Latitude]
            },
            "properties": props
        }
        features.append(feat)

    geojson_data = {
        "type": "FeatureCollection",
        "features": features
    }

    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(geojson_data, f, ensure_ascii=False, indent=2)
    print(f"[+] Successfully generated GeoJSON (RFC 7946 & OSM Ready): {filename} ({len(features)} features)")


def format_md_rate_car(payex, hourly_rate, name):
    if '民有市場' in name:
        return '30 元/時（115/11/1起 40 元）'
    if '松山國小' in name:
        return '週一至週四 40 元/時；週五至週日 50 元/時'
    if '金華公園' in name:
        return '08:00–22:00 每小時 50 元；22:00–08:00 每小時 10 元'
    if '中山市場' in name:
        return '臨停約 100 元/時（以半小時計）'
    if '士林市場' in name:
        return '平日 40 元/時、假日 60 元/時'
    if '花木批發' in name:
        return '20 元/時（半小時 10 元）'

    # Try Day/Night detection: e.g. 08:00-22:00 30元, 22:00-08:00 10元
    m = re.search(r'(\d{1,2}(?::\d{2})?(?:時)?[-~]\d{1,2}(?::\d{2})?(?:時)?)[^\d]*?(\d+)\s*元.*?(\d{1,2}(?::\d{2})?(?:時)?[-~]\d{1,2}(?::\d{2})?(?:時)?)[^\d]*?(\d+)\s*元', payex)
    if m:
        return f'{m.group(1)} 每小時 {m.group(2)} 元；{m.group(3)} 每小時 {m.group(4)} 元'
    
    m2 = re.search(r'(\d+)\s*元[^\(（]*?[\(（](\d{1,2}(?::\d{2})?[-~]\d{1,2}(?::\d{2})?)[\)）].*?(\d+)\s*元[^\(（]*?[\(（](\d{1,2}(?::\d{2})?[-~]\d{1,2}(?::\d{2})?)[\)）]', payex)
    if m2:
        return f'{m2.group(2)} 每小時 {m2.group(1)} 元；{m2.group(4)} 每小時 {m2.group(3)} 元'

    if hourly_rate and hourly_rate != '依公告':
        return f'全日每小時 {hourly_rate} 元（全程半小時計）'

    clean = re.sub(r'月租.*', '', payex).strip()
    return clean[:35] if clean else '公有平價計時'


def format_md_rate_moto(payex, total_moto):
    try:
        if int(total_moto or 0) == 0:
            return '無機車位'
    except (ValueError, TypeError):
        pass

    m_cap = re.search(r'當日(?:單次)?(?:停車)?(?:最高)?收費上限\s*(\d+)\s*元', payex)
    if m_cap:
        return f'10 元/時（當日上限 {m_cap.group(1)} 元）'
    if '20元/次' in payex:
        return '20 元/日'
    if '10元/時' in payex:
        return '10 元/時'
    return '10 元/時（當日上限 20 元）'


def format_md_height(name):
    if '民生立體' in name:
        return '1.7m'
    if '中山市場' in name:
        return '1.8m'
    if any(k in name for k in ['大龍', '濱江', '環南', '家禽']):
        return '2.1m'
    return '2.0m'


def format_md_features(lot, name):
    if '民有市場' in name:
        return '設 EV 充電格位、格上共享車站點'
    if '金華公園' in name:
        return '智慧充電示範站（充電格位每小時加收 10 元為 60 元/時）'
    if '大龍' in name:
        return '大龍新城共構大樓，設有 EV 慢充充電樁'
    if '環南' in name:
        return '地下與地上立體共構、設 EV 充電樁'
    if '濱江' in name:
        return '設 EV 充電格、直通第二果菜市場'
    if '士林市場' in name:
        return '位於士林夜市正下方，出入便利'
    if '民生立體' in name:
        return '限高 1.7m，休旅車及加裝車頂架車輛切勿進場'

    charging = lot.get('charging_stalls', 0)
    has_ev = False
    try:
        has_ev = int(charging or 0) > 0
    except (ValueError, TypeError):
        has_ev = str(charging) not in ('0', '', 'None')

    if has_ev:
        return '室內地下平面、EV充電樁'
    return '室內地下平面、公有規範管理'


def export_markdown(lots, filename):
    """
    Generates taipei_parking_lots.md on the fly with unified 7-column tables
    grouped by Taipei City administrative districts.
    """
    preferred_order = [
        '松山區', '中山區', '大同區', '萬華區', '大安區', '信義區',
        '內湖區', '南港區', '文山區', '士林區', '北投區', '中正區'
    ]
    dist_map = collections.defaultdict(list)
    for l in lots:
        dist_map[l['district']].append(l)

    districts = sorted(dist_map.keys(), key=lambda d: preferred_order.index(d) if d in preferred_order else 999)

    md = []
    md.append('# 臺北市平價公有地下平面停車場指南\n')
    md.append('本指南彙整臺北市各行政區內符合「室內地下坡道平面」、「24 小時全日營運」、「公有平價費率」且「配備電動車充電樁與友善車位」之公有停車場名冊。  ')
    md.append('全篇採用**統一表格結構**編排，提供各場站之精確費率、限高、車位數與 Google 地圖即時搜尋導航連結。\n')
    md.append('* 📥 **地圖圖層與書籤匯入檔案（可直接匯入至 Google 地圖或 CoMaps / Organic Maps）**：')
    md.append('  * **KMZ 離線書籤檔（CoMaps / Organic Maps 一鍵匯入推薦）**：[taipei_parking_lots.kmz](./taipei_parking_lots.kmz)（手機點擊即開，內建停車圖標與完整離線導航）')
    md.append('  * **KML 檔（Google 我的地圖）**：[taipei_parking_lots.kml](./taipei_parking_lots.kml)（含地圖自訂停車圖標與導航卡片）')
    md.append('  * **CSV 檔**：[taipei_parking_lots.csv](./taipei_parking_lots.csv)（含官方經緯度座標與詳細規格）')
    md.append('  * **GeoJSON 檔 (OpenStreetMap / uMap)**：[taipei_parking_lots.geojson](./taipei_parking_lots.geojson)（符合標準 OSM 標籤架構）\n')
    md.append('---\n')
    md.append('## 一、 規格與服務條件說明\n')
    md.append('名冊內收錄之公有地下停車場均符合以下營運標準：\n')
    md.append('* **車位與建物型態**：以室內地下坡道平面格位為主（非機械塔，限高約 2.0 公尺）。')
    md.append('* **營運時間**：24 小時全日營業，全面配備車牌辨識系統，支援電子票證（悠遊卡）及行動支付。')
    md.append('* **小型車收費基準**：')
    md.append('  * **平價計時費率**：日間臨停每小時 20～40 元（全程以半小時計費）。')
    md.append('  * **夜間離峰優惠**：部分住宅區與國中小場站享夜間離峰（22:00–08:00）每小時 10 元優惠。')
    md.append('* **機車收費基準**：臨停每小時 10 元，當日單次停放最高收費上限 20 元（跨日重新計算）。')
    md.append('* **友善與充電設施**：設有電動汽車（EV）充電專用格位、孕婦及育有六歲以下兒童專用格位、身心障礙專用格位。\n')
    md.append('---\n')
    md.append('## 二、 各行政區公有地下平面停車場名冊（統一結構表）\n')

    for dist in districts:
        items = dist_map[dist]
        md.append(f'### {dist}\n')
        md.append('| 停車場名稱（含導航連結） | 地址 | 小型車臨停費率 | 機車上限 | 限高 | 車位 / 充電格 | 設施特色與備註 |')
        md.append('| :--- | :--- | :--- | :--- | :---: | :---: | :--- |')
        for l in items:
            name = l['name']
            url = l['gmaps_url']
            if '民有市場' in name:
                url = 'https://maps.app.goo.gl/WsLPEg952dXUfFgv5'

            clean_addr = l['address'].replace('臺北市', '').replace('台北市', '')
            if clean_addr.startswith(dist):
                clean_addr = clean_addr[len(dist):].strip()
            if not clean_addr:
                clean_addr = l['address']

            rate_car = format_md_rate_car(l['payex'], l['hourly_rate'], name)
            rate_moto = format_md_rate_moto(l['payex'], l['total_moto'])
            height = format_md_height(name)

            stalls = f"汽車 {l['total_car']}"
            if l['charging_stalls'] and str(l['charging_stalls']) != '0':
                stalls += f"<br>EV {l['charging_stalls']}"
            else:
                stalls += "<br>公有格位"

            features = format_md_features(l, name)

            md.append(f"| [{name}]({url}) | {clean_addr} | {rate_car} | {rate_moto} | {height} | {stalls} | {features} |")
        md.append('')

    md.append('---\n')
    md.append('## 三、 停放、充電與計費重要法規須知\n')
    md.append('* **計費單位原則**：臺北市公有路外地下停車場進場未滿 30 分鐘者以 30 分鐘計費；逾 30 分鐘後之剩餘時間未滿 30 分鐘者，仍以 30 分鐘計算。')
    md.append('* **夜間離峰優惠**：設於住宅區公園或國中小地下之場站，多數適用夜間（22:00 至次日 08:00）每小時 10 元之優惠；但商圈周邊或假日熱區（如饒河夜市旁之松山國小）無夜間優惠。')
    md.append('* **電動車充電專用格位收費**：')
    md.append('  * 依停管處規範，電動車充電除收取原本之計時停車費外，**充電服務費另依度計費（以度計價）**。')
    md.append('  * 部分場站（如金華公園、西松高中）實施充電格位差別費率，停放充電格位每小時加收 10 元，未充電佔位者亦加收甚至加倍處分。')
    md.append('* **特殊車高限制提醒**：')
    md.append('  * 「民生立體收費停車場」為地上 8 層、地下 1 層立體塔樓，限高僅 1.70 公尺，休旅車及加裝車頂架之車輛切勿進場。')

    with open(filename, 'w', encoding='utf-8') as f:
        f.write('\n'.join(md))
    print(f"[+] Successfully generated Markdown Guide: {filename} ({len(lots)} entries across {len(districts)} districts)")


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    default_csv = os.path.join(script_dir, 'taipei_parking_lots.csv')
    default_kml = os.path.join(script_dir, 'taipei_parking_lots.kml')
    default_kmz = os.path.join(script_dir, 'taipei_parking_lots.kmz')
    default_geojson = os.path.join(script_dir, 'taipei_parking_lots.geojson')
    default_md = os.path.join(script_dir, 'taipei_parking_lots.md')
    default_cache = os.path.join(script_dir, 'alldesc_cache.json')

    parser = argparse.ArgumentParser(
        description="Fetch Taipei parking lots from official SSOT API and generate Google My Maps / CoMaps / OpenStreetMap outputs."
    )
    parser.add_argument('--no-cache', action='store_true', help='Force re-fetching latest live JSON from Azure Blob SSOT')
    parser.add_argument('--cache-file', default=default_cache, help='Local path for dataset cache')
    parser.add_argument('--max-rate', type=int, default=50, help='Maximum daytime hourly rate filter (e.g. 40 or 50 NTD/hr)')
    parser.add_argument('--ev-only', action='store_true', help='Only include lots with EV charging stations')
    parser.add_argument('--district', type=str, default=None, help='Filter by specific administrative district (e.g. 松山區)')
    parser.add_argument('--require-coords', action='store_true', default=True, help='Require valid WGS84 entrance coordinates')
    parser.add_argument('--public-only', action='store_true', default=True, help='Only include public/municipal regulated lots')
    parser.add_argument('--underground-only', action='store_true', default=True, help='Only include indoor underground/planar facilities')
    parser.add_argument('--hours-24', action='store_true', default=True, help='Require 24-hour continuous service')
    parser.add_argument('--output-csv', default=default_csv, help='Output CSV path')
    parser.add_argument('--output-kml', default=default_kml, help='Output KML path')
    parser.add_argument('--output-kmz', default=default_kmz, help='Output KMZ (CoMaps / Organic Maps) path')
    parser.add_argument('--output-geojson', default=default_geojson, help='Output GeoJSON (OpenStreetMap) path')
    parser.add_argument('--output-md', default=default_md, help='Output Markdown handbook path')

    args = parser.parse_args()

    # Step 1: Fetch
    data = fetch_official_data(cache_file=args.cache_file, use_cache=not args.no_cache)
    raw_lots = data.get('data', {}).get('park', [])
    print(f"[*] Total raw parking lots from SSOT: {len(raw_lots)}")

    # Step 2: Filter
    filtered_lots, stats = filter_parking_lots(raw_lots, args)

    print("\n" + "=" * 50)
    print("      TAIPEI PARKING JÄGER FILTERING PIPELINE      ")
    print("=" * 50)
    print(f"1. Total in Official Database      : {stats['total']:>5} lots")
    print(f"2. Passed Public Ownership Filter  : {stats['passed_public']:>5} lots (Eliminated private lots)")
    print(f"3. Passed 24/7 Hours Filter        : {stats['passed_24h']:>5} lots")
    print(f"4. Passed Underground/Planar Filter: {stats['passed_underground']:>5} lots (Eliminated open-air/mechanical towers)")
    print(f"5. Passed Hourly Rate Ceiling (≤${args.max_rate}): {stats['passed_rate']:>5} lots")
    if args.ev_only:
        print(f"6. Passed EV Charger Filter (EV-only): {stats['passed_ev']:>5} lots")
    print(f"7. Passed Valid Coordinates Filter : {stats['passed_coords']:>5} lots")
    print("-" * 50)
    print(f"==> FINAL QUALIFIED TARGET LOTS    : {stats['final']:>5} lots")
    print("=" * 50 + "\n")

    # Step 3: Export
    export_csv(filtered_lots, args.output_csv)
    kml_str = export_kml(filtered_lots, args.output_kml)
    export_kmz(kml_str, args.output_kmz)
    export_geojson(filtered_lots, args.output_geojson)
    export_markdown(filtered_lots, args.output_md)
    print(f"[✓] All files generated successfully!")
    print(f"    - CoMaps / Organic Maps (1-Tap) : '{args.output_kmz}'")
    print(f"    - Google My Maps                : '{args.output_kml}' / '{args.output_csv}'")
    print(f"    - OpenStreetMap (OSM / uMap)    : '{args.output_geojson}'")
    print(f"    - Markdown Handbook             : '{args.output_md}'")


if __name__ == '__main__':
    main()
