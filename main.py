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
QQ_THUMB_PATH = ""
MAX_VIDEO_SIZE_MB = 200  # **最大允许下载的视频大小**

# **确保文件夹存在**
os.makedirs(VIDEO_PATH, exist_ok=True)
os.makedirs(THUMBNAIL_PATH, exist_ok=True)

# **Bilibili Cookies 文件**
COOKIES_FILE = os.path.join(PLUGIN_PATH, "cookies.txt")


@register("bili_downloader", "YourName", "解析 & 下载 Bilibili 视频", "2.7.0")
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
            return  # 解析失败，直接返回

        # **获取视频信息**
        video_info = await self.get_bilibili_video_info(bv_id)
        if not video_info:
            return

        (title, up_name, duration, view_count, likes, coins, shares, comments, cover_url) = video_info  # ✅ 确保匹配 9 个值

        # **获取视频大小**
        video_size_mb = await self.get_bilibili_video_size(video_url) or 0  # 避免 `None` 类型错误
        logger.info(f"🎥 视频大小: {video_size_mb:.2f} MB")

        # **创建合并转发消息**
        nodes = Nodes([])

        # **拼接文本**
        video_info_text = (
            f"🎬 标题: {title}\n"
            f"👤 UP主: {up_name}\n"
            f"🔢 播放量: {view_count}\n"
            f"❤️ 点赞: {likes}\n"
            f"🏆 投币: {coins}\n"
            f"🔄 分享: {shares}\n"
            f"💬 评论: {comments}"
        )
        nodes.nodes.append(Node(uin=event.get_self_id(), name="BiliBot", content=[Plain(video_info_text)]))

        # **如果视频大小 > 200MB，直接发送信息，不下载**
        logger.info(f"📏 获取到视频大小: {video_size_mb:.2f} MB (最大允许: {MAX_VIDEO_SIZE_MB} MB)")
        if video_size_mb > MAX_VIDEO_SIZE_MB:
            nodes.nodes.append(Node(uin=event.get_self_id(), name="BiliBot", content=[Plain(f"❌ 视频大小超过 {MAX_VIDEO_SIZE_MB}MB，无法下载")]))
            yield event.chain_result([nodes])
            return  # **直接返回，不下载视频**

        # **下载视频**
        video_save_path = os.path.join(VIDEO_PATH, f"{bv_id}.mp4")
        try:
            await self.download_bilibili_video(video_url, video_save_path)  # ✅ 异步下载

            # **计算 MP4 的 MD5 作为封面名称**
            video_md5 = self.calculate_md5(video_save_path)

            # **如果 `QQ_THUMB_PATH` 非空，处理封面**
            if QQ_THUMB_PATH:
                thumbnail_save_path = os.path.join(THUMBNAIL_PATH, f"{video_md5}.png")
                qq_thumb_path = os.path.join(QQ_THUMB_PATH, f"{video_md5}_0.png")

                # **下载封面**
                if await self.download_thumbnail(cover_url, thumbnail_save_path):
                    shutil.copy(thumbnail_save_path, qq_thumb_path)

            # **追加视频到消息**
            nodes.nodes.append(Node(uin=event.get_self_id(), name="BiliBot", content=[Video.fromFileSystem(video_save_path)]))

            # **延迟删除视频文件**
            asyncio.create_task(self.cleanup_files(bv_id, video_md5))

        except Exception as e:
            logger.error(f"❌ 视频下载失败: {str(e)}")

        # **发送合并转发消息**
        yield event.chain_result([nodes])

    async def get_bilibili_video_size(self, video_url):
        """获取 B 站视频大小 (MB)"""
        try:
            ydl_opts = {
                "quiet": True,
                "simulate": True,
                "no_warnings": True,
                "format": "bv*+ba/bv*",  # 选取视频 + 音频
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(video_url, download=False)

                if "_type" in info and info["_type"] == "playlist":
                    info = info["entries"][0]  # 取第一集的详情

                file_size = info.get("filesize") or info.get("filesize_approx", 0)
                return file_size / (1024 * 1024) if file_size else 0

        except Exception as e:
            logger.error(f"❌ 获取视频大小失败: {str(e)}")
            return 0

    async def cleanup_files(self, bv_id, video_md5):
        """延迟 10 秒后删除下载的视频和封面"""
        await asyncio.sleep(10)

        video_file = os.path.join(VIDEO_PATH, f"{bv_id}.mp4")
        thumbnail_file = os.path.join(THUMBNAIL_PATH, f"{video_md5}_0.png")

        if os.path.exists(video_file):
            os.remove(video_file)
            print(f"✅ 已删除视频文件: {video_file}")

        if os.path.exists(thumbnail_file):
            os.remove(thumbnail_file)
            print(f"✅ 已删除封面文件: {thumbnail_file}")

    async def download_bilibili_video(self, url: str, output_path: str):
        """✅ 异步下载 B 站视频"""
        ydl_opts = {
            "format": "bv*[ext=mp4]+ba[ext=m4a]/bv*+ba",
            "outtmpl": output_path,
            "merge_output_format": "mp4",
            "quiet": True,
            "no_warnings": True,
            "cookiefile": COOKIES_FILE,
        }
        await asyncio.to_thread(self._run_yt_dlp, ydl_opts, url)

    def _run_yt_dlp(self, ydl_opts, url):
        """同步运行 yt-dlp"""
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.extract_info(url, download=True)

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

    def calculate_md5(self, file_path: str) -> str:
        """计算文件 MD5 值"""
        hasher = hashlib.md5()
        with open(file_path, "rb") as f:
            while chunk := f.read(8192):
                hasher.update(chunk)
        return hasher.hexdigest()
    
    async def get_bilibili_video_info(self, bv_id: str):
            """获取 B 站视频信息"""
            try:
                credential = Credential(sessdata=None)
                v = video.Video(bvid=bv_id, credential=credential)
                info = await v.get_info()
                stat = info["stat"]

                return (
                    info.get("title", "未知标题"),   # 标题
                    info["owner"].get("name", "未知UP主"),  # UP主
                    f"{stat.get('duration', 0) // 60}:{stat.get('duration', 0) % 60:02d}",  # 时长
                    stat.get("view", 0),  # 播放量
                    stat.get("like", 0),  # 点赞
                    stat.get("coin", 0),  # 投币
                    stat.get("share", 0),  # 分享
                    stat.get("reply", 0),  # 评论
                    info.get("pic", ""),  # 封面链接
                )

            except Exception as e:
                logger.error(f"❌ 解析 B 站视频信息失败: {str(e)}")
                return None  # 避免 NoneType 崩溃