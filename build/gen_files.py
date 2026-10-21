# -*- coding: utf-8 -*-
"""遍历 dist/wxbot 生成 WiX v4/v6 语法的 files.wxs（ComponentGroup=WxbotFiles）。"""
import hashlib
import os
import sys

ROOT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "../dist/wxbot")


def fid(path, prefix):
    h = hashlib.md5(path.encode("utf-8")).hexdigest()[:10]
    return f"{prefix}_{h}"


comp_defs = []       # (dir_id, cid, xml)
dir_nodes = []       # (parent_dir_id, dir_id, name)
children = {}


def walk(rel, parent_id):
    base = os.path.join(ROOT, rel) if rel else ROOT
    entries = sorted(os.listdir(base))
    here = parent_id
    for e in entries:
        full = os.path.join(base, e)
        relpath = os.path.join(rel, e) if rel else e
        if os.path.isdir(full):
            did = fid(relpath, "d")
            dir_nodes.append((parent_id, did, e))
            children.setdefault(parent_id, []).append(did)
            walk(relpath, did)
        else:
            cid = fid(relpath, "c")
            src = os.path.abspath(os.path.join(ROOT, relpath)).replace("\\", "/")
            comp_defs.append((here, cid,
                              f'<Component Id="{cid}">'
                              f'<File Id="{fid(relpath, "f")}" Source="{src}" KeyPath="yes"/>'
                              f"</Component>"))


walk("", "INSTALLDIR")

lines = ['<Wix xmlns="http://wixtoolset.org/schemas/v4/wxs">',
         "<Fragment>",
         '  <DirectoryRef Id="INSTALLDIR">']

dir_name = {did: name for _, did, name in dir_nodes}


def emit_dir(did, indent):
    pad = "  " * indent
    lines.append(f'{pad}<Directory Id="{did}" Name="{dir_name[did]}">')
    for d, cid, xml in comp_defs:
        if d == did:
            lines.append(f"{pad}  {xml}")
    for child in children.get(did, []):
        emit_dir(child, indent + 1)
    lines.append(f"{pad}</Directory>")


for d, cid, xml in comp_defs:
    if d == "INSTALLDIR":
        lines.append("    " + xml)
for child in children.get("INSTALLDIR", []):
    emit_dir(child, 2)
lines.append("  </DirectoryRef>")
lines.append("</Fragment>")
lines.append("<Fragment>")
lines.append('  <ComponentGroup Id="WxbotFiles">')
for _, cid, _ in comp_defs:
    lines.append(f'    <ComponentRef Id="{cid}"/>')
lines.append("  </ComponentGroup>")
lines.append("</Fragment>")
lines.append("</Wix>")

with open("files.wxs", "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print(f"files.wxs: {len(comp_defs)} files, {len(dir_nodes)} dirs")
