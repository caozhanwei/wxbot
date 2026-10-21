# -*- coding: utf-8 -*-
"""原图下载示例脚本

演示如何通过UI自动化触发微信下载原图。

用法：
    python demo_original_image.py                    # 下载最近一张图片的原图
    python demo_original_image.py 群名 --count 5     # 下载指定会话最近5张图片
"""

import argparse

from wechatauto import WeChatDB, MediaDownloader


def main():
    parser = argparse.ArgumentParser(description="原图下载示例")
    parser.add_argument("chat", nargs="?", default="送你挖银子", help="会话名称/昵称（默认：送你挖银子）")
    parser.add_argument("--count", type=int, default=1, help="下载图片数量（默认：1）")
    parser.add_argument("--timeout", type=float, default=30, help="每张图片等待超时（秒，默认：30）")
    parser.add_argument("--save-dir", default=None, help="保存目录（默认：~/Documents/wechatauto_media）")
    args = parser.parse_args()

    db = WeChatDB()
    md = MediaDownloader(db, save_dir=args.save_dir)

    # 查找会话（支持昵称和wxid）
    chat_name = args.chat
    contact = db.search_contact(chat_name)
    if contact:
        chat_name = contact[0].get("username", chat_name)
        print(f"找到会话: {contact[0].get('nickname', chat_name)} ({chat_name})")
    else:
        print(f"未找到联系人 '{args.chat}'，尝试直接使用名称...")

    # 获取最新消息，筛选图片消息
    print(f"正在查找 {chat_name} 中的最新图片消息...")
    messages = db.get_messages(chat_name, limit=100)  # 获取最近100条消息
    image_ids = [m["local_id"] for m in messages if m["type"] == "图片"]

    if not image_ids:
        print(f"未找到 {chat_name} 中的图片消息")
        return

    # 取最新的N张（已经是降序，取前N个）
    image_ids = image_ids[:args.count]
    print(f"找到 {len(image_ids)} 张图片，开始下载原图...")

    success = 0
    for i, local_id in enumerate(image_ids, 1):
        print(f"\n[{i}/{len(image_ids)}] 图片 local_id={local_id}...")

        # 先问一句本机有哪一档：以前这里用「文件大于 100KB」当原图判据，
        # 而真原图中位数只有 91.5KB——一半会把压缩版当成原图、把原图当成没下完。
        st = md.image_status(chat_name, local_id)
        print(f"  本机档位: {st['tiers'] or '无'} reason={st['reason']}")
        if st["reason"] == "ok":
            out = md.download_image(chat_name, local_id, save_dir=args.save_dir,
                                    tier="original")
            if out:
                print(f"  ✓ 原图已在本地，直接解密: {out}")
                success += 1
                continue

        # 本机没有原图，通过UI点击触发微信下载
        print("  本机没有原图，通过UI点击触发下载...")
        out = md.download_image_original(
            chat_name, local_id,
            save_dir=args.save_dir,
            timeout=args.timeout,
            chat_name=args.chat
        )
        if out:
            print(f"  ✓ 原图下载成功: {out}")
            success += 1
        else:
            st2 = md.image_status(chat_name, local_id)
            print(f"  ✗ 下载失败（现在档位: {st2['tiers'] or '无'} reason={st2['reason']}）")

    print(f"\n完成: {success}/{len(image_ids)} 张原图下载成功")


if __name__ == "__main__":
    main()
