#!/usr/bin/env python3
"""Build a small, reproducible set of Xray geodata from MetaCubeX + RU extras."""
import argparse
import base64
import hashlib
import json
import re
import time
import urllib.request
from pathlib import Path

from geodata_tools import deduplicate, domains, encode_domains, entries, fields, write_entries

ROOT = Path(__file__).resolve().parents[1]
URL = 'https://github.com/MetaCubeX/meta-rules-dat/releases/download/latest/'
CATEGORIES = {
    'ru': ['category-ru'],
    'apple': ['apple'],
    'games': ['steam', 'category-games', 'category-game-platforms-download'],
    'games-proxy': ['ubisoft', 'blizzard'],
    'trackers': ['category-public-tracker'],
    'ads': ['category-ads-all'],
}


def parse_extra(rule):
    prefix, value = rule.split(':', 1)
    return {'domain': 2, 'full': 3, 'regexp': 1, 'keyword': 0}[prefix], value


def matches(rules, host):
    return any((kind == 2 and (host == value or host.endswith('.' + value)))
               or (kind == 3 and host == value)
               or (kind == 0 and value in host)
               or (kind == 1 and re.search(value, host)) for kind, value in rules)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input-dir', type=Path, help='Directory containing upstream geosite.dat/geoip.dat')
    parser.add_argument('--base-url', help='HTTPS directory containing the published geosite.dat and geoip.dat')
    args = parser.parse_args()
    source = args.input_dir or ROOT / '.cache'
    if not args.input_dir:
        source.mkdir(exist_ok=True)
        for name in ('geosite.dat', 'geoip.dat'):
            request = urllib.request.Request(URL + name, headers={'User-Agent': 'happ-routing-builder'})
            with urllib.request.urlopen(request, timeout=90) as response:
                data = response.read()
            (source / name).write_bytes(data)

    geo = entries(source / 'geosite.dat')
    ip = entries(source / 'geoip.dat')
    extras = json.loads((ROOT / 'source/extra-domains.json').read_text())
    result = {}
    for name, sources in CATEGORIES.items():
        data = [item for tag in sources for item in domains(geo[tag])]
        data.extend(parse_extra(item) for item in extras.get(name, []))
        result[name] = deduplicate(data)
        if not result[name]:
            raise ValueError('Empty category: ' + name)

    # The routing rule gives games-proxy priority. Also remove covered game domains
    # from the direct category to avoid choosing domestic DNS for those platforms.
    proxy = result['games-proxy']
    result['games'] = [item for item in result['games']
                       if not (item in proxy or (item[0] in (2, 3) and matches(proxy, item[1])))]
    for host in ('ozon.ru', 'api.ozon.ru', 'ozon.com', 'ozonusercontent.com',
                 'kinopoisk.ru', 'yandex.ru', 'yastatic.net', '2gis.ru', '2gis.com',
                 'test.ru', 'test.su', 'test.xn--p1ai'):
        if not matches(result['ru'], host):
            raise ValueError('RU coverage regression: ' + host)
    for host in ('store.steampowered.com', 'cdn.steamcontent.com'):
        if not matches(result['games'], host):
            raise ValueError('Steam coverage regression: ' + host)
    for host in ('ubisoft.com', 'connect.ubisoft.com', 'battle.net', 'blizzard.com'):
        if not matches(proxy, host) or matches(result['games'], host):
            raise ValueError('Proxy exception regression: ' + host)

    write_entries(ROOT / 'geosite.dat', [encode_domains(name, rules) for name, rules in result.items()])
    write_entries(ROOT / 'geoip.dat', [ip['ru']])
    if len(entries(ROOT / 'geoip.dat')) != 1 or set(entries(ROOT / 'geosite.dat')) != set(CATEGORIES):
        raise ValueError('Generated category inventory mismatch')
    # Guard against accidentally publishing a new huge ad/RU list.
    for name, max_size in (('geosite.dat', 1_000_000), ('geoip.dat', 2_000_000)):
        if (ROOT / name).stat().st_size > max_size:
            raise ValueError(name + ' exceeds the size budget; inspect upstream changes')

    manifest = {
        'sources': {name: {'url': URL + name,
                          'sha256': hashlib.sha256((source / name).read_bytes()).hexdigest()}
                    for name in ('geosite.dat', 'geoip.dat')},
        'geosite_categories': {name: {'sources': CATEGORIES[name], 'entries': len(data)}
                               for name, data in result.items()},
        'geoip_ru_entries': sum(field == 2 for field, wire, value in fields(ip['ru'])),
        'files': {name: {'bytes': (ROOT / name).stat().st_size,
                          'sha256': hashlib.sha256((ROOT / name).read_bytes()).hexdigest()}
                  for name in ('geosite.dat', 'geoip.dat', 'xray-json.json')},
    }
    (ROOT / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    profile_path = ROOT / 'happ-routing.json'
    profile = json.loads(profile_path.read_text())
    if args.base_url:
        if not args.base_url.startswith('https://'):
            raise ValueError('base-url must use HTTPS')
        profile['Geositeurl'] = args.base_url.rstrip('/') + '/geosite.dat'
        profile['Geoipurl'] = args.base_url.rstrip('/') + '/geoip.dat'
    profile['LastUpdated'] = str(int(time.time()))
    profile_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + '\n')
    encoded = base64.b64encode(json.dumps(profile, ensure_ascii=False, separators=(',', ':')).encode()).decode()
    header = 'happ://routing/onadd/' + encoded
    (ROOT / 'routing-header.txt').write_text(header + '\n')
    response_rule = {
        'name': 'Happ Clients',
        'description': 'Return Xray JSON and compact routing geodata to Happ',
        'enabled': True,
        'operator': 'AND',
        'conditions': [{'headerName': 'user-agent', 'operator': 'REGEX',
                        'value': '^Happ(?:/|$)', 'caseSensitive': False}],
        'responseType': 'XRAY_JSON',
        'responseModifications': {'headers': [{'key': 'routing', 'value': header}],
                                  'applyHeadersToEnd': True},
    }
    (ROOT / 'remnawave-happ-rule.json').write_text(json.dumps(response_rule, indent=2) + '\n')
    print(json.dumps({'categories': {name: len(data) for name, data in result.items()},
                      'files': {name: value['bytes'] for name, value in manifest['files'].items()}}, indent=2))


if __name__ == '__main__':
    main()
