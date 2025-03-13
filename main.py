import subprocess
import sys

def install_missing_packages():
    """ 自动安装缺失的 Python 依赖库（静默安装） """
    required_packages = [
        "httpx", "aiohttp", "yt-dlp", "Pillow",
        "bilibili-api-python", "tqdm"
    ]
    for package in required_packages:
        try:
            __import__(package)
        except ImportError:
            print(f"⚠️ {package} 未安装，正在自动安装...")
            subprocess.run(
                [sys.executable, "-m", "pip", "install", package],
                stdout=subprocess.DEVNULL,  # 隐藏标准输出
                stderr=subprocess.DEVNULL,  # 隐藏错误输出
                check=True
            )

install_missing_packages()

import re
import httpx
import os
import asyncio
import yt_dlp
import hashlib
import shutil
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api.message_components import Node, Nodes, Plain, Video, Image
from astrbot.api import logger
from bilibili_api import Credential, video

# **Bilibili 正则表达式**
BILI_VIDEO_PATTERN = r"(https?:\/\/)?www\.bilibili\.com\/video\/(BV\w+|av\d+)\/?"
BILI_SHORT_LINK_PATTERN = r"(https?://(?:b23\.tv|bili2233\.cn)/[A-Za-z\d._?%&+\-=\/#]+)"

# **存储路径**
PLUGIN_PATH = "data/plugins/astrbot_plugin_bv/"
VIDEO_PATH = os.path.join(PLUGIN_PATH, "bilibili_videos/")
THUMBNAIL_PATH = os.path.join(PLUGIN_PATH, "bilibili_thumbnails/")
QQ_THUMB_PATH = "C:\\Users\\Yukikaze\\Documents\\Tencent Files\\3870158425\\nt_qq\\nt_data\\Video\\2025-03\\Thumb"  # 设置为空则跳过相关操作
os.makedirs(VIDEO_PATH, exist_ok=True)
os.makedirs(THUMBNAIL_PATH, exist_ok=True)

# **确保 `QQ_THUMB_PATH` 目录存在（如果非空）**
if QQ_THUMB_PATH:
    os.makedirs(QQ_THUMB_PATH, exist_ok=True)

# **Bilibili Cookies 文件**
COOKIES_FILE = os.path.join(PLUGIN_PATH, "cookies.txt")

# **Bilibili Headers**
BILIBILI_HEADER = {
    'User-Agent': 'Mozilla/5.0',
    'referer': 'https://www.bilibili.com',
}

@register("bili_downloader", "YourName", "解析 & 下载 Bilibili 视频", "2.4.0")
class BiliDownloader(Star):
    def __init__(self, context: Context):
        super().__init__(context)

    @filter.regex(BILI_VIDEO_PATTERN)
    async def handle_bili_video(self, event: AstrMessageEvent):
        """监听 B 站视频链接 & 解析 & 下载"""
        msg = event.message_str

        # **解析标准链接**
        match = re.search(BILI_VIDEO_PATTERN, msg)
        if match:
            video_url = match.group(0)
            bv_id = match.group(2)
        else:
            # **解析短链接**
            video_url = await self.resolve_short_link(msg)
            if not video_url:
                return
            bv_id = await self.extract_bv_id(video_url)

        if not bv_id:
            logger.error(f"❌ 无法解析 BV 号: {video_url}")
            return

        # **获取视频信息**
        video_info = await self.get_bilibili_video_info(bv_id)
        if not video_info:
            return

        title, up_name, duration, view_count, likes, coins, shares, comments, cover_url = video_info

        # **计算 MD5 作为文件名**
        video_save_path = os.path.join(VIDEO_PATH, f"{bv_id}.mp4")

        # **下载视频**
        try:
            await asyncio.to_thread(self.download_bilibili_video, video_url, video_save_path)
        except Exception as e:
            logger.error(f"❌ 视频下载失败: {str(e)}")
            return

        # **计算 MP4 的 MD5 作为封面名称**
        video_md5 = self.calculate_md5(video_save_path)

        # **如果 `QQ_THUMB_PATH` 非空，处理封面**
        if QQ_THUMB_PATH:
            thumbnail_save_path = os.path.join(THUMBNAIL_PATH, f"{video_md5}.png")
            qq_thumb_path = os.path.join(QQ_THUMB_PATH, f"{video_md5}_0.png")

            # **下载封面**
            if await self.download_thumbnail(cover_url, thumbnail_save_path):
                shutil.copy(thumbnail_save_path, qq_thumb_path)

        # **创建合并转发消息**
        nodes = Nodes([])

        # 🎬 **标题**
        nodes.nodes.append(Node(
            uin=event.get_self_id(),
            name="BiliBot",
            content=[Plain(f"🎬 标题: {title}")]
        ))

        # 👤 **UP主**
        nodes.nodes.append(Node(
            uin=event.get_self_id(),
            name="BiliBot",
            content=[Plain(f"👤 UP主: {up_name}")]
        ))

        # 🔢 **播放量**
        nodes.nodes.append(Node(
            uin=event.get_self_id(),
            name="BiliBot",
            content=[Plain(f"🔢 播放量: {view_count}")]
        ))

        # ❤️ **点赞**
        nodes.nodes.append(Node(
            uin=event.get_self_id(),
            name="BiliBot",
            content=[Plain(f"❤️ 点赞: {likes}")]
        ))

        # 🏆 **投币**
        nodes.nodes.append(Node(
            uin=event.get_self_id(),
            name="BiliBot",
            content=[Plain(f"🏆 投币: {coins}")]
        ))

        # 🔄 **分享**
        nodes.nodes.append(Node(
            uin=event.get_self_id(),
            name="BiliBot",
            content=[Plain(f"🔄 分享: {shares}")]
        ))

        # 💬 **评论**
        nodes.nodes.append(Node(
            uin=event.get_self_id(),
            name="BiliBot",
            content=[Plain(f"💬 评论: {comments}")]
        ))

        # 🎥 **视频**
        nodes.nodes.append(Node(
            uin=event.get_self_id(),
            name="BiliBot",
            content=[Video.fromFileSystem(video_save_path)]
        ))

        # **发送合并转发消息**
        yield event.chain_result([nodes])

        # 📌 **在发送完成后再清理文件**
        asyncio.create_task(self.cleanup_files(bv_id, video_md5))
    
    async def cleanup_files(self, bv_id, video_md5):
        """延迟 10 秒后删除下载的视频和封面"""
        await asyncio.sleep(10)  # 延迟 10 秒

        video_file = os.path.join(VIDEO_PATH, f"{bv_id}.mp4")
        thumbnail_file = os.path.join(THUMBNAIL_PATH, f"{video_md5}_0.png")  # MD5 命名的封面

        # ✅ **确保先发送后删除**
        if os.path.exists(video_file):
            os.remove(video_file)
            print(f"✅ 已删除视频文件: {video_file}")

        if os.path.exists(thumbnail_file):
            os.remove(thumbnail_file)
            print(f"✅ 已删除封面文件: {thumbnail_file}")

    async def get_bilibili_video_info(self, bv_id: str):
        """获取 B 站视频信息"""
        try:
            credential = Credential(sessdata=None)
            v = video.Video(bvid=bv_id, credential=credential)
            info = await v.get_info()
            stat = info["stat"]

            return (
                info.get("title", "未知标题"),
                info["owner"].get("name", "未知UP主"),
                f"{stat.get('duration', 0) // 60}:{stat.get('duration', 0) % 60:02d}",
                stat.get("view", 0),
                stat.get("like", 0),
                stat.get("coin", 0),
                stat.get("share", 0),
                stat.get("reply", 0),
                info.get("pic", ""),
            )

        except Exception as e:
            logger.error(f"❌ 解析 B 站视频信息失败: {str(e)}")
            return None

    async def download_thumbnail(self, url: str, save_path: str):
        """下载视频封面"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url)
                if response.status_code == 200:
                    with open(save_path, "wb") as f:
                        f.write(response.content)
                    return True
        except Exception as e:
            logger.error(f"❌ 下载封面失败: {str(e)}")
        return False

    def download_bilibili_video(self, url: str, output_path: str):
        """下载 B 站视频"""
        ydl_opts = {
            "format": "bv*[ext=mp4]+ba[ext=m4a]/bv*+ba",
            "outtmpl": output_path,
            "merge_output_format": "mp4",
            "quiet": True,  # 关闭所有日志
            "no_warnings": True,  # 关闭警告
            "progress_hooks": [lambda d: None],  # 关闭进度显示
            "cookiefile": COOKIES_FILE,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.extract_info(url, download=True)

    def calculate_md5(self, file_path: str) -> str:
        """计算文件 MD5 值"""
        hasher = hashlib.md5()
        with open(file_path, "rb") as f:
            while chunk := f.read(8192):
                hasher.update(chunk)
        return hasher.hexdigest()

