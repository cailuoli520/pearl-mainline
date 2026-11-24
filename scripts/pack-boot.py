#!/usr/bin/env python3
"""Package same-build Linux payloads using stock Android boot templates."""

import argparse
from pathlib import Path
import subprocess
import sys


def replace_argument(arguments, option, value):
    arguments[arguments.index(option) + 1] = str(value)


def replace_vendor_ramdisk(arguments, info, ramdisk):
    if info.header_version <= 3:
        replace_argument(arguments, '--vendor_ramdisk', ramdisk)
        return

    # Preserve recovery/DLKM payloads and every table entry's name/type/board ID.
    platform_names = {
        entry[0] for entry in info.vendor_ramdisk_table if entry[3] in (0, 1)
    }
    replaced = False
    for index, option in enumerate(arguments[:-1]):
        if option == '--vendor_ramdisk_fragment':
            if Path(arguments[index + 1]).name in platform_names:
                arguments[index + 1] = str(ramdisk)
                replaced = True
    if not replaced:
        raise ValueError('stock vendor_boot has no platform ramdisk fragment')


def footer_arguments(avb, stock, output, work):
    image = avb.ImageHandler(str(stock), read_only=True)
    try:
        _, header, descriptors, partition_size = avb.Avb()._parse_image(image)
    finally:
        image._image.close()
    digest = next(d for d in descriptors if isinstance(d, avb.AvbHashDescriptor))
    arguments = [
        'add_hash_footer', '--image', str(output), '--algorithm', 'NONE',
        '--partition_size', str(partition_size),
        '--partition_name', digest.partition_name,
        '--hash_algorithm', digest.hash_algorithm, '--salt', digest.salt.hex(),
        '--rollback_index', str(header.rollback_index),
        '--flags', str(header.flags),
        '--rollback_index_location', str(header.rollback_index_location),
    ]
    if digest.flags & 1:
        arguments.append('--do_not_use_ab')
    if not digest.digest:
        arguments.append('--use_persistent_digest')
    # Copy property bytes, not the old hash descriptor, digest or signature.
    for index, descriptor in enumerate(descriptors):
        if isinstance(descriptor, avb.AvbPropertyDescriptor):
            value_file = work / f'avb-property-{index}.bin'
            value_file.write_bytes(descriptor.value)
            arguments.extend(['--prop_from_file', f'{descriptor.key}:{value_file}'])
    return arguments


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mkbootimg-dir', type=Path, required=True)
    parser.add_argument('--avbtool-dir', type=Path, required=True)
    options = parser.parse_args()
    mkbootimg_dir = options.mkbootimg_dir.resolve()
    avbtool_dir = options.avbtool_dir.resolve()
    sys.path[:0] = [str(mkbootimg_dir), str(avbtool_dir)]
    import unpack_bootimg
    import avbtool

    root = Path(__file__).resolve().parents[1]
    out = root / 'out'
    out.mkdir(exist_ok=True)
    ramdisk = out / 'initramfs.cpio.lz4'
    for name in ('boot', 'vendor_boot'):
        stock = root / 'stock' / f'{name}.img'
        output = out / f'{name}.img'
        work = root / '.work' / 'boot-pack' / name
        work.mkdir(parents=True, exist_ok=True)
        info = unpack_bootimg.unpack_bootimg(str(stock), str(work))
        arguments = info.format_mkbootimg_argument()
        if name == 'boot':
            # Stock GKI signatures cover the old kernel, not this unsigned Linux payload.
            # mkbootimg therefore generates a zero signature length for the new image.
            replace_argument(arguments, '--kernel', out / 'Image.gz')
            replace_argument(arguments, '--ramdisk', ramdisk)
            output_option = '--output'
        else:
            replace_vendor_ramdisk(arguments, info, ramdisk)
            output_option = '--vendor_boot'
        footer = footer_arguments(avbtool, stock, output, work)
        print(f'Packing {name}.img from stock template and same-build payloads', flush=True)
        subprocess.run([sys.executable, str(mkbootimg_dir / 'mkbootimg.py'),
                        *arguments, output_option, str(output)], check=True)
        subprocess.run([sys.executable, str(avbtool_dir / 'avbtool.py'),
                        *footer], check=True)


if __name__ == '__main__':
    main()
