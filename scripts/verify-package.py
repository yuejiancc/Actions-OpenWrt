#!/usr/bin/env python3
"""Fail closed on ABI mismatch; inspect the packaged ELF, not just the source."""
import hashlib
import gzip
import io
import json
import os
from pathlib import Path
import re
import shutil
import struct
import tarfile

ROOT = Path(__file__).resolve().parents[1]
DIAG = ROOT / 'diagnostics'
FUNC = 'rx_802_3_data_frm_announce'


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def kernel_config_changes(expected, actual):
    def parse(text):
        return dict(line.split('=', 1) for line in text.splitlines()
                    if line.startswith('CONFIG_') and '=' in line)
    baseline, built = parse(expected), parse(actual)
    # Host compiler discovery metadata is not a target-kernel setting.
    return [{'symbol': key, 'running': baseline.get(key, 'n'), 'built': built.get(key, 'n')}
            for key in sorted(baseline.keys() | built.keys())
            if key != 'CONFIG_PLUGIN_HOSTCC' and baseline.get(key, 'n') != built.get(key, 'n')]


def verify_kernel_config():
    baseline = gzip.decompress((ROOT / 'running-kernel.config.gz').read_bytes()).decode()
    configs = list((ROOT / 'openwrt/build_dir').glob('*/linux-mediatek_mt7981/linux-5.4.284/.config'))
    require(len(configs) == 1, 'Cannot uniquely locate the built kernel configuration')
    changes = kernel_config_changes(baseline, configs[0].read_text())
    (DIAG / 'kernel-config-diff.json').write_text(json.dumps(changes, indent=2) + '\n')
    require(not changes, f'Built kernel differs from the running router: {changes}')


def package_parts(blob):
    # OpenWrt 21.02 uses a gzip tar container; also accept standard ar IPKs.
    if not blob.startswith(b'!<arch>\n'):
        with tarfile.open(fileobj=io.BytesIO(blob), mode='r:*') as archive:
            return {Path(m.name).name: archive.extractfile(m).read()
                    for m in archive if m.isfile()}
    offset, result = 8, {}
    while offset < len(blob):
        header = blob[offset:offset + 60]
        require(len(header) == 60 and header[58:60] == b'`\n', 'Invalid IPK ar header')
        size = int(header[48:58])
        name = header[:16].decode().strip().rstrip('/')
        result[name] = blob[offset + 60:offset + 60 + size]
        offset += 60 + size + size % 2
    return result


def tar_files(blob):
    with tarfile.open(fileobj=io.BytesIO(blob), mode='r:*') as archive:
        return {m.name.lstrip('./'): archive.extractfile(m).read()
                for m in archive if m.isfile()}


def elf_report(blob, inspect_function=False):
    require(blob[:6] == b'\x7fELF\x02\x01', 'Expected a little-endian ELF64 module')
    header = struct.unpack_from('<16sHHIQQQIHHHHHH', blob)
    require(header[1] == 1 and header[2] == 183, 'Expected an AArch64 relocatable module')
    sections = [struct.unpack_from('<IIQQQQIIQQ', blob, header[6] + i * header[11])
                for i in range(header[12])]

    def string(table, offset):
        return table[offset:table.index(b'\0', offset)].decode('utf-8', 'replace')

    def symbols(section):
        strings = sections[section[6]]
        table = blob[strings[4]:strings[4] + strings[5]]
        result = []
        for offset in range(section[4], section[4] + section[5], section[9]):
            sym = struct.unpack_from('<IBBHQQ', blob, offset)
            result.append((string(table, sym[0]), *sym[1:]))
        return result

    names = sections[header[13]]
    table = blob[names[4]:names[4] + names[5]]
    info = next(s for s in sections if string(table, s[0]) == '.modinfo')
    modinfo = blob[info[4]:info[4] + info[5]].split(b'\0')
    vermagic = next(x.split(b'=', 1)[1].decode().strip()
                    for x in modinfo if x.startswith(b'vermagic='))
    result = {'sha256': hashlib.sha256(blob).hexdigest(), 'vermagic': vermagic}
    if not inspect_function:
        return result
    syms = symbols(next(s for s in sections if s[1] == 2))
    func = next(s for s in syms if s[0] == FUNC)
    require(func[5] > 0, 'Receive function has no code')
    refs = []
    for section in sections:
        if section[1] != 4 or section[7] != func[3]:
            continue
        targets = symbols(sections[section[6]])
        for offset in range(section[4], section[4] + section[5], section[9]):
            addr, info, _ = struct.unpack_from('<QQq', blob, offset)
            if func[4] <= addr < func[4] + func[5]:
                refs.append({'offset': hex(addr - func[4]), 'symbol': targets[info >> 32][0]})
    require(refs, 'Missing function relocations; cannot verify compiled fix')
    require(not any(x['symbol'] == 'MacTableLookup' for x in refs),
            'Old fallible MacTableLookup call is still in the receive function')
    require(any(x['symbol'] == 'RTMPCheckWPAframe' for x in refs),
            'Port security check is missing from the receive function')
    result.update(function=FUNC, function_size=func[5], relocations=refs)
    return result


def main():
    DIAG.mkdir(exist_ok=True)
    verify_kernel_config()
    packages = list((ROOT / 'openwrt/bin').rglob('kmod-mt_wifi_*.ipk'))
    require(len(packages) == 1, f'Expected one driver IPK, found {len(packages)}')
    package = packages[0]
    parts = package_parts(package.read_bytes())
    control_blob = next(value for key, value in parts.items() if key.startswith('control.tar'))
    data_blob = next(value for key, value in parts.items() if key.startswith('data.tar'))
    control = tar_files(control_blob)['control'].decode()
    (DIAG / 'package-control.txt').write_text(control)
    fields = dict(line.split(': ', 1) for line in control.splitlines() if ': ' in line)
    require(fields['Package'] == 'kmod-mt_wifi', 'Unexpected package')
    require(fields['Architecture'] == 'aarch64_cortex-a53', 'Wrong package architecture')
    require(fields['Version'] == '5.4.284+7.6.7.3-mt7981-2', 'Unexpected driver version')
    kernel = re.search(r'(?:^|,\s*)kernel\s*\(=\s*([^\)]+)\)', fields['Depends'])
    require(kernel is not None, 'Missing exact kernel dependency')
    require(kernel.group(1).strip() == os.environ['EXPECTED_KERNEL'],
            f'Kernel ABI mismatch: built {kernel.group(1)}, expected {os.environ["EXPECTED_KERNEL"]}')
    modules = {name: blob for name, blob in tar_files(data_blob).items() if name.endswith('.ko')}
    require({Path(n).name for n in modules} == {'mt_wifi.ko', 'mtk_warp_proxy.ko'},
            'Unexpected module set in driver package')
    reports = {}
    for name, blob in modules.items():
        report = elf_report(blob, Path(name).name == 'mt_wifi.ko')
        require(report['vermagic'] == os.environ['EXPECTED_VERMAGIC'], f'Vermagic mismatch: {name}')
        reports[name] = report
    provenance = {
        'source_url': os.environ['SOURCE_URL'], 'source_commit': os.environ['SOURCE_COMMIT'],
        'configuration_commit': os.environ['GITHUB_SHA'], 'run_url':
        f'https://github.com/{os.environ["GITHUB_REPOSITORY"]}/actions/runs/{os.environ["GITHUB_RUN_ID"]}',
        'kernel_dependency': kernel.group(1).strip(), 'package': package.name,
        'package_sha256': hashlib.sha256(package.read_bytes()).hexdigest(), 'modules': reports,
        'status': 'Build and ABI checks passed; router runtime validation still required',
    }
    (DIAG / 'verification.json').write_text(json.dumps(provenance, indent=2) + '\n')
    destination = ROOT / 'artifacts/driver'
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copy2(package, destination / package.name)
    shutil.copy2(DIAG / 'verification.json', destination)
    shutil.copy2(ROOT / 'DRIVER-FIX.md', destination / 'README.md')
    (destination / 'sha256sums').write_text(f'{provenance["package_sha256"]}  {package.name}\n')
    print(json.dumps(provenance, indent=2))
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary, 'a') as file:
            file.write(f'### Patched driver compiled and verified\n\n'
                       f'- Package: `{package.name}`\n- Kernel dependency: `{kernel.group(1).strip()}`\n'
                       '- Old receive-path MacTableLookup call removed; port security check retained.\n'
                       '- No router changes were made. Runtime validation remains pending.\n')


if __name__ == '__main__':
    main()
