#!/usr/bin/env python3
"""Build podcast: synthesize missing episode audio with edge-tts, then rebuild feed.xml."""
import asyncio
import html
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EPISODES = ROOT / "episodes"
DOCS = ROOT / "docs"
AUDIO = DOCS / "audio"

BASE_URL = os.environ.get("BASE_URL", "https://toyamatakeshi-sketch.github.io/beauty-biz-daily").rstrip("/")
OWNER_EMAIL = os.environ.get("OWNER_EMAIL", "toyama.takeshi@sukettollc.com")
VOICE = os.environ.get("TTS_VOICE", "ja-JP-NanamiNeural")
JST = timezone(timedelta(hours=9))

SHOW_TITLE = "ビューティービジネス・デイリー"
SHOW_DESC = (
    "美容室・ネイルサロン・ヘッドスパ・アイサロン・エステなど、"
    "美容サロン経営に役立つ最新ニュースを毎朝お届けする番組です。"
)
SHOW_AUTHOR = SHOW_TITLE


async def synthesize(text: str, out_path: Path) -> None:
    import edge_tts

    extra_ca = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
    if extra_ca and os.path.exists(extra_ca):
        import ssl

        ctx = ssl.create_default_context(cafile=extra_ca)
        edge_tts.communicate._SSL_CTX = ctx
        edge_tts.voices._SSL_CTX = ctx

    last_err = None
    for attempt in range(5):
        try:
            tts = edge_tts.Communicate(text, VOICE, rate="+8%")
            await tts.save(str(out_path))
            if out_path.stat().st_size > 10000:
                return
            raise RuntimeError("output too small")
        except Exception as e:  # noqa: BLE001
            last_err = e
            out_path.unlink(missing_ok=True)
            await asyncio.sleep(10 * (attempt + 1))
    raise RuntimeError(f"TTS failed after retries: {last_err}")


def mp3_duration(path: Path) -> int:
    try:
        from mutagen.mp3 import MP3

        return int(MP3(str(path)).info.length)
    except Exception:  # noqa: BLE001
        return 0


def episode_meta(date_str: str):
    md = EPISODES / f"{date_str}.md"
    title_suffix = ""
    desc = SHOW_DESC
    if md.exists():
        content = md.read_text(encoding="utf-8")
        m = re.search(r"^# (.+)$", content, re.M)
        if m:
            title_suffix = ""
        heads = re.findall(r"^## \d+\. (.+)$", content, re.M)
        if heads:
            desc = " / ".join(heads)
        links = re.findall(r"^- (.+?): (\S+)$", content, re.M)
        if links:
            desc += "\n\n出典:\n" + "\n".join(f"{t}: {u}" for t, u in links)
    d = datetime.strptime(date_str, "%Y-%m-%d")
    title = f"{SHOW_TITLE} {d.year}年{d.month}月{d.day}日"
    return title + title_suffix, desc


def build_feed(dates: list[str]) -> str:
    items = []
    for ds in sorted(dates, reverse=True):
        mp3 = AUDIO / f"{ds}.mp3"
        if not mp3.exists():
            continue
        title, desc = episode_meta(ds)
        d = datetime.strptime(ds, "%Y-%m-%d").replace(hour=6, tzinfo=JST)
        url = f"{BASE_URL}/audio/{ds}.mp3"
        dur = mp3_duration(mp3)
        items.append(f"""    <item>
      <title>{html.escape(title)}</title>
      <description><![CDATA[{desc}]]></description>
      <enclosure url="{url}" length="{mp3.stat().st_size}" type="audio/mpeg"/>
      <guid isPermaLink="false">{url}</guid>
      <pubDate>{format_datetime(d)}</pubDate>
      <itunes:duration>{dur}</itunes:duration>
      <itunes:explicit>false</itunes:explicit>
    </item>""")
    now = format_datetime(datetime.now(timezone.utc))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>{html.escape(SHOW_TITLE)}</title>
    <link>{BASE_URL}/</link>
    <description><![CDATA[{SHOW_DESC}]]></description>
    <language>ja</language>
    <lastBuildDate>{now}</lastBuildDate>
    <atom:link href="{BASE_URL}/feed.xml" rel="self" type="application/rss+xml"/>
    <itunes:author>{html.escape(SHOW_AUTHOR)}</itunes:author>
    <itunes:owner>
      <itunes:name>{html.escape(SHOW_AUTHOR)}</itunes:name>
      <itunes:email>{OWNER_EMAIL}</itunes:email>
    </itunes:owner>
    <itunes:image href="{BASE_URL}/cover.jpg"/>
    <itunes:category text="Business"/>
    <itunes:explicit>false</itunes:explicit>
{os.linesep.join(items)}
  </channel>
</rss>
"""


def prune_old(dates: list[str], keep_days: int = 90) -> list[str]:
    """Delete audio older than keep_days so the GitHub Pages site stays under its size limit."""
    if len(dates) <= 1:
        return dates
    newest = datetime.strptime(max(dates), "%Y-%m-%d")
    kept = []
    for ds in dates:
        d = datetime.strptime(ds, "%Y-%m-%d")
        mp3 = AUDIO / f"{ds}.mp3"
        if (newest - d).days > keep_days:
            mp3.unlink(missing_ok=True)
            print(f"pruned old audio: {ds}")
        else:
            kept.append(ds)
    return kept


def main() -> int:
    AUDIO.mkdir(parents=True, exist_ok=True)
    dates = sorted(p.stem for p in EPISODES.glob("*.txt"))
    dates = prune_old(dates)
    for ds in dates:
        mp3 = AUDIO / f"{ds}.mp3"
        if mp3.exists():
            continue
        text = (EPISODES / f"{ds}.txt").read_text(encoding="utf-8").strip()
        print(f"Synthesizing {ds} ({len(text)} chars) with {VOICE}...")
        asyncio.run(synthesize(text, mp3))
        print(f"  -> {mp3} ({mp3.stat().st_size} bytes)")
    (DOCS / "feed.xml").write_text(build_feed(dates), encoding="utf-8")
    print("feed.xml rebuilt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
