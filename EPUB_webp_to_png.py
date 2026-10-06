#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EPUB WebP -> PNG 批量转换脚本
- 支持拖放多个文件到脚本图标 / 双击后拖入控制台
- 支持 HTML属性 / CSS url() / SVG 中的引用
- 自动备份到 webp_backup，输出保持原文件名
"""
import os
import sys
import shutil
import zipfile
import tempfile
import re
import shlex
import datetime
from pathlib import Path
from PIL import Image
from lxml import etree

WEBP_EXTENSIONS = {'.webp'}
HTML_EXTENSIONS = {'.html', '.xhtml', '.htm'}
CSS_EXTENSIONS = {'.css'}
SVG_EXTENSIONS = {'.svg'}

REFERENCE_EXTENSIONS = HTML_EXTENSIONS | CSS_EXTENSIONS | SVG_EXTENSIONS

DIAGNOSE_EXTENSIONS = REFERENCE_EXTENSIONS | {
    '.xml', '.opf', '.ncx', '.js', '.json', '.txt'
}

OPF_NAMESPACES = {
    'opf': 'http://www.idpf.org/2007/opf',
    'dc': 'http://purl.org/dc/elements/1.1/'
}

BACKUP_DIR_NAME = 'webp_backup'


def is_webp_file(file_path):
    return file_path.suffix.lower() in WEBP_EXTENSIONS


def collect_files_by_extensions(root_dir, extensions):
    result = []
    for root, _, files in os.walk(root_dir):
        for f in files:
            p = Path(root) / f
            if p.suffix.lower() in extensions:
                result.append(p)
    return result


def collect_reference_files(root_dir):
    return collect_files_by_extensions(root_dir, REFERENCE_EXTENSIONS)


def collect_webp_files(root_dir):
    return collect_files_by_extensions(root_dir, WEBP_EXTENSIONS)


def convert_webp_to_png(webp_path):
    try:
        png_path = webp_path.with_suffix('.png')
        with Image.open(webp_path) as img:
            has_alpha = (img.mode in ('RGBA', 'LA', 'PA') or 'transparency' in img.info)
            
            if has_alpha:
                img = img.convert('RGBA')
            else:
                # 无透明通道：强制转为 RGB
                img = img.convert('RGB')
            img.save(png_path, 'PNG', compress_level=9, optimize=True)
        return png_path
    except Exception as e:
        print(f"⚠️  转换失败 {webp_path.name}: {e}")
        return None


def read_text_file(path):
    with open(path, 'r', encoding='utf-8', errors='replace', newline='') as f:
        return f.read()


def write_text_file(path, text):
    with open(path, 'w', encoding='utf-8', newline='') as f:
        f.write(text)


def make_filename_patterns(old_name):
    patterns = []
    replacements = []

    # 变体1：原始文件名（真实空格）
    old_esc = re.escape(old_name)
    patterns.append(re.compile(
        r"(?<![A-Za-z0-9._-])" + old_esc + r"(?![A-Za-z0-9._-])"
    ))
    replacements.append(old_name)  # 占位，实际替换时用 new_name

    # 变体2：空格 -> %20
    if ' ' in old_name:
        url_encoded = old_name.replace(' ', '%20')
        url_esc = re.escape(url_encoded)
        patterns.append(re.compile(
            r"(?<![A-Za-z0-9._-])" + url_esc + r"(?![A-Za-z0-9._-])"
        ))
        replacements.append(url_encoded)

    return patterns


def get_replacement_names(old_name, new_name):
    """
    根据旧文件名和新文件名，生成对应的替换名列表。
    顺序与 make_filename_patterns 一致。
    """
    names = [new_name]
    if ' ' in old_name:
        names.append(new_name.replace(' ', '%20'))
    return names


def replace_filename_in_reference_files(reference_files, old_name, new_name):
    """
    在 HTML/XHTML/CSS/SVG 中替换文件名。
    同时处理真实空格和 %20 编码两种引用形式。
    """
    patterns = make_filename_patterns(old_name)
    new_names = get_replacement_names(old_name, new_name)
    count = 0

    for ref_file in reference_files:
        try:
            content = read_text_file(ref_file)
        except Exception as e:
            print(f"⚠️  无法读取 {ref_file.name}: {e}")
            continue

        file_count = 0
        for pattern, new_n in zip(patterns, new_names):
            # 用默认参数固定 new_n，避免闭包问题
            content, n = pattern.subn(lambda m, _r=new_n: _r, content)
            file_count += n

        if file_count > 0:
            try:
                write_text_file(ref_file, content)
                count += file_count
            except Exception as e:
                print(f"⚠️  无法写入 {ref_file.name}: {e}")

    return count


def find_reference_locations(root_dir, old_name, extensions=None):
    if extensions is None:
        extensions = DIAGNOSE_EXTENSIONS

    patterns = make_filename_patterns(old_name)
    locations = []
    root = Path(root_dir)

    for p in root.rglob('*'):
        if not p.is_file():
            continue
        if p.suffix.lower() not in extensions:
            continue
        try:
            content = read_text_file(p)
        except Exception:
            continue

        if not any(pat.search(content) for pat in patterns):
            continue

        rel = p.relative_to(root).as_posix()
        for i, line in enumerate(content.splitlines(), 1):
            if any(pat.search(line) for pat in patterns):
                snippet = line.strip()
                if len(snippet) > 180:
                    snippet = snippet[:180] + '...'
                locations.append((rel, i, snippet))

        if len(locations) >= 20:
            break

    return locations


def update_content_opf(opf_file, old_href, new_href):
    try:
        parser = etree.XMLParser()
        tree = etree.parse(opf_file, parser)
        root = tree.getroot()

        manifest = root.find('.//opf:manifest', OPF_NAMESPACES)
        if manifest is not None:
            updated = False
            for item in manifest.findall('opf:item', OPF_NAMESPACES):
                href = item.get('href', '')
                # OPF 中 href 可能是原始空格，也可能是 %20
                if href == old_href or href == old_href.replace(' ', '%20'):
                    item.set('href', new_href)
                    item.set('media-type', 'image/png')
                    print(f"✅ OPF: {href} → {new_href}")
                    updated = True

            if updated:
                tree.write(opf_file, encoding='utf-8', xml_declaration=True)
                return True

        return False
    except Exception as e:
        print(f"⚠️  OPF 更新失败 {opf_file.name}: {e}")
        return False


def find_opf_file(tmpdir):
    container_path = Path(tmpdir) / 'META-INF' / 'container.xml'
    if container_path.exists():
        try:
            tree = etree.parse(container_path)
            rootfile = tree.find(
                './/rootfile[@media-type="application/oebps-package+xml"]'
            )
            if rootfile is not None:
                full_path = rootfile.get('full-path')
                if full_path:
                    opf = Path(tmpdir) / full_path
                    if opf.exists():
                        return opf
        except Exception as e:
            print(f"⚠️  解析 container.xml 失败: {e}")

    candidates = list(Path(tmpdir).rglob('content.op?'))
    return candidates[0] if candidates else None


def repack_epub(source_dir, output_epub):
    source_dir = Path(source_dir)
    output_epub = Path(output_epub)

    with zipfile.ZipFile(output_epub, 'w', zipfile.ZIP_DEFLATED) as zf:
        mime = source_dir / 'mimetype'
        if mime.exists():
            zf.writestr(
                'mimetype',
                mime.read_bytes(),
                compress_type=zipfile.ZIP_STORED
            )
        else:
            zf.writestr(
                'mimetype',
                'application/epub+zip',
                compress_type=zipfile.ZIP_STORED
            )

        for root, dirs, files in os.walk(source_dir):
            dirs.sort()
            for f in sorted(files):
                if f == 'mimetype':
                    continue
                full = Path(root) / f
                arcname = full.relative_to(source_dir).as_posix()
                zf.write(full, arcname)

    print(f"📦 已打包: {output_epub}")


def make_unique_backup_path(backup_dir, filename):
    candidate = backup_dir / filename
    if not candidate.exists():
        return candidate

    p = Path(filename)
    stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    candidate = backup_dir / f"{p.stem}_backup_{stamp}{p.suffix}"

    n = 1
    while candidate.exists():
        candidate = backup_dir / f"{p.stem}_backup_{stamp}_{n}{p.suffix}"
        n += 1

    return candidate


def backup_original(epub_path):
    try:
        backup_dir = epub_path.parent / BACKUP_DIR_NAME
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = make_unique_backup_path(backup_dir, epub_path.name)
        shutil.copy2(epub_path, backup_path)
        print(f"💾 已备份原文件: {backup_path}")
        return backup_path
    except Exception as e:
        print(f"⚠️ 备份失败，跳过该文件: {e}")
        return None


def expand_path_to_epubs(path):
    path = Path(path)
    result = []
    try:
        if path.is_dir():
            for p in path.rglob('*'):
                if p.is_file() and p.suffix.lower() == '.epub':
                    result.append(p)
            result.sort()
        elif path.is_file() and path.suffix.lower() == '.epub':
            result.append(path)
        elif path.exists():
            print(f"⚠️ 跳过非 EPUB 文件: {path}")
        else:
            print(f"⚠️ 路径不存在: {path}")
    except Exception as e:
        print(f"⚠️ 读取路径失败 {path}: {e}")
    return result


def clean_token(token):
    token = token.strip()
    if len(token) >= 2 and token[0] == token[-1] and token[0] in ('"', "'"):
        token = token[1:-1]
    return token


def parse_dropped_line(line):
    line = line.strip()
    if not line:
        return []

    try:
        if os.name == 'nt':
            parts = shlex.split(line, posix=False)
        else:
            parts = shlex.split(line, posix=True)
    except ValueError:
        parts = re.split(r'\s+', line)

    paths = []
    for part in parts:
        part = clean_token(part)
        if not part:
            continue
        if part.lower() in ('q', 'quit', 'exit'):
            continue
        paths.extend(expand_path_to_epubs(Path(part)))

    return paths


def process_epub(epub_path, overwrite=False, backup=False):
    epub_path = Path(epub_path).resolve()

    if not epub_path.exists():
        print(f"❌ 文件不存在: {epub_path}")
        return

    if epub_path.suffix.lower() != '.epub':
        print(f"⚠️ 跳过非 EPUB 文件: {epub_path.name}")
        return

    output_path = epub_path if overwrite else epub_path.with_name(
        epub_path.stem + '_webp2png.epub'
    )

    backup_path = None
    repack_started = False
    repack_finished = False

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            print("📂 解压到临时目录...")
            with zipfile.ZipFile(epub_path, 'r') as zf:
                zf.extractall(tmpdir)

            webp_files = collect_webp_files(tmpdir)
            if not webp_files:
                print("🔍 未找到 .webp 图片，文件保持不变，也不创建备份。")
                return

            if backup:
                backup_path = backup_original(epub_path)
                if not backup_path:
                    return
                output_path = epub_path

            reference_files = collect_reference_files(tmpdir)
            print(
                f"🖼️  找到 {len(webp_files)} 个 WebP 图片，"
                f"{len(reference_files)} 个 HTML/XHTML/CSS/SVG 文件"
            )

            opf_file = find_opf_file(tmpdir)
            if not opf_file:
                print("⚠️  未找到 content.opf，仍可替换 HTML/CSS，但无法更新 manifest")

            total_replaced = 0

            for webp_path in webp_files:
                png_path = convert_webp_to_png(webp_path)
                if not png_path:
                    continue

                webp_name = webp_path.name
                png_name = png_path.name

                replaced_count = replace_filename_in_reference_files(
                    reference_files,
                    webp_name,
                    png_name
                )

                if replaced_count > 0:
                    print(
                        f"✅ 替换了 {replaced_count} 处引用: "
                        f"{webp_name} → {png_name}"
                    )
                    total_replaced += replaced_count

                opf_updated_this = False
                if opf_file:
                    rel_to_opf_old = os.path.relpath(
                        webp_path, opf_file.parent
                    ).replace(os.sep, '/')
                    rel_to_opf_new = os.path.relpath(
                        png_path, opf_file.parent
                    ).replace(os.sep, '/')
                    opf_updated_this = update_content_opf(
                        opf_file, rel_to_opf_old, rel_to_opf_new
                    )

                if replaced_count == 0:
                    if opf_updated_this:
                        print(
                            f"ℹ️  HTML/CSS/SVG 中未找到引用，"
                            f"但 OPF 中存在该资源: {webp_name}"
                        )
                    else:
                        print(f"ℹ️  未找到引用: {webp_name}，正在诊断...")
                        locations = find_reference_locations(tmpdir, webp_name)
                        if locations:
                            print("🔎 检测到这些位置仍包含文件名：")
                            for rel, line_no, snippet in locations[:5]:
                                print(f"   {rel}:{line_no}: {snippet}")
                        else:
                            print("   未在常见文件中检测到，可能是未使用图片。")

                try:
                    webp_path.unlink()
                except Exception as e:
                    print(f"⚠️  无法删除 {webp_path.name}: {e}")

            if total_replaced == 0 and len(webp_files) > 0:
                print(
                    "❓ 警告：所有 WebP 都未在 HTML/XHTML/CSS/SVG 中找到直接引用"
                    "（可能仅在 OPF/manifest 或未使用）"
                )

            repack_started = True
            repack_epub(tmpdir, output_path)
            repack_finished = True

            print(f"🎉 完成！输出: {output_path}")
            if backup_path:
                print(f"🗂️ 原文件备份: {backup_path}")

    except Exception as e:
        print(f"❌ 处理失败: {e}")

        if backup_path and backup_path.exists():
            if repack_started and not repack_finished:
                try:
                    shutil.copy2(backup_path, epub_path)
                    print(f"♻️ 已尝试从备份恢复原文件: {epub_path}")
                except Exception as e2:
                    print(
                        f"⚠️ 自动恢复失败，请手动从备份恢复: "
                        f"{backup_path} ({e2})"
                    )
            elif not repack_started:
                try:
                    backup_path.unlink()
                    print("🧹 尚未覆盖原文件，已删除本次备份。")
                except Exception:
                    pass
            else:
                print("⚠️ 输出已完成，但后续步骤出现异常；备份仍保留。")


def process_many(epub_paths):
    seen = set()
    tasks = []

    for p in epub_paths:
        try:
            rp = Path(p).resolve()
        except Exception:
            continue

        if rp in seen:
            continue
        seen.add(rp)
        tasks.append(rp)

    if not tasks:
        print("没有可处理的 EPUB 文件。")
        return

    print(
        f"🚀 共 {len(tasks)} 个 EPUB，将保持原文件名输出，"
        f"并把原文件备份到各自目录的 {BACKUP_DIR_NAME} 文件夹。"
    )

    for i, epub in enumerate(tasks, 1):
        print(f"\n[{i}/{len(tasks)}] {epub.name}")
        process_epub(epub, overwrite=True, backup=True)


def main():
    args = sys.argv[1:]
    no_pause = '--no-pause' in args
    file_args = [a for a in args if not a.startswith('-')]

    if file_args:
        paths = []
        for a in file_args:
            paths.extend(expand_path_to_epubs(Path(a)))

        process_many(paths)

        if os.name == 'nt' and not no_pause:
            try:
                if sys.stdin is not None and sys.stdin.isatty():
                    input("\n处理完成，按回车关闭窗口...")
            except Exception:
                pass
        return

    print("=" * 60)
    print("EPUB WebP -> PNG 批量处理脚本")
    print("=" * 60)
    print("用法 1：把一个或多个 .epub 文件拖到本脚本文件图标上。")
    print("用法 2：双击运行本脚本，然后把 .epub 文件拖到控制台窗口，按回车。")
    print("提示：输入 q 后回车可退出。")

    while True:
        try:
            line = input("\n请拖入文件（可多个），然后按回车: ")
        except (EOFError, KeyboardInterrupt):
            break

        if line.strip().lower() in ('q', 'quit', 'exit'):
            break

        paths = parse_dropped_line(line)

        if paths:
            process_many(paths)
        else:
            print("没有识别到有效 EPUB 文件。可以拖文件，也可以拖包含 EPUB 的文件夹。")

    if os.name == 'nt':
        try:
            if sys.stdin is not None and sys.stdin.isatty():
                input("\n按回车退出...")
        except Exception:
            pass


if __name__ == '__main__':
    main()
