from __future__ import annotations

from bearbless.config import Config
from bearbless.runtime.adb import AdbClient


class PackageResolutionError(RuntimeError):
    pass


def package_installed(adb: AdbClient, package: str) -> bool:
    result = adb.shell("pm", "path", package)
    return result.ok and isinstance(result.stdout, str) and result.stdout.strip().startswith("package:")


def list_installed_packages(adb: AdbClient) -> list[str]:
    # User-installed packages are the meaningful launch candidates. Excluding
    # hundreds of framework packages keeps model selection bounded without
    # hard-coding one workflow per app.
    result = adb.shell("pm", "list", "packages", "-3")
    if not result.ok or not isinstance(result.stdout, str):
        raise PackageResolutionError("could not enumerate installed Android packages")
    return sorted({line.removeprefix("package:").strip() for line in result.stdout.splitlines() if line.startswith("package:")})


SYSTEM_APP_ALIASES: dict[str, str] = {
    # High-risk communication tasks must never let a model guess between
    # similarly named Tencent packages (QQ, QQ Music, WeChat, Mail, etc.).
    "QQ": "com.tencent.mobileqq",
    "网易云音乐": "com.netease.cloudmusic",
    "网易云": "com.netease.cloudmusic",
    "QQ音乐": "com.tencent.qqmusic",
    "YouTube": "com.google.android.youtube",
    "Youtube": "com.google.android.youtube",
    "youtube": "com.google.android.youtube",
    "youtobe": "com.google.android.youtube",
    "Youtobe": "com.google.android.youtube",
    "油管": "com.google.android.youtube",
    "美团": "com.sankuai.meituan",
    "Wolt": "com.wolt.android",
    "wolt": "com.wolt.android",
    "WOLT": "com.wolt.android",
    "夸克": "com.quark.browser",
    "系统时钟": "com.huawei.deskclock",
    "时钟": "com.huawei.deskclock",
    "闹钟": "com.huawei.deskclock",
    "系统设置": "com.android.settings",
    "设置": "com.android.settings",
}

MAP_PACKAGE_CANDIDATES: tuple[tuple[str, str], ...] = (
    ("高德地图", "com.autonavi.minimap"),
    ("百度地图", "com.baidu.BaiduMap"),
    ("腾讯地图", "com.tencent.map"),
    ("花瓣地图", "com.huawei.maps.app"),
)


def resolve_explicit_app_alias(goal: str, adb: AdbClient) -> str | None:
    """Resolve unambiguous user-facing app names before asking a model.

    System apps are absent from ``pm list packages -3`` and therefore need a
    small, verified alias layer. Longer aliases win so a phrase such as
    ``系统设置`` cannot be shadowed by ``设置``.
    """
    folded_goal = goal.casefold()
    # Communication intent wins over app names occurring inside the message
    # payload (for example “Wolt推荐：…”). Letting payload text select Wolt
    # would route a confirmed QQ send back into the source app.
    if (
        "qq" in folded_goal
        and any(term in goal for term in ("发消息", "发送消息", "发信息", "发送信息", "发给", "编辑消息草稿"))
        and package_installed(adb, "com.tencent.mobileqq")
    ):
        return "com.tencent.mobileqq"
    for alias, package in MAP_PACKAGE_CANDIDATES:
        if alias in goal and package_installed(adb, package):
            return package
    for alias in sorted(SYSTEM_APP_ALIASES, key=len, reverse=True):
        package = SYSTEM_APP_ALIASES[alias]
        if alias.casefold() in folded_goal and package_installed(adb, package):
            return package
    return None


def resolve_browser_package(config: Config, adb: AdbClient) -> str:
    candidates = [config.browser_package] if config.browser_package else []
    # Huawei Browser produced black shadow frames and transient movement on
    # Display 0 on the verified ELS-AN00 device. Keep it available only as an
    # explicit operator override; prefer packages proven on the shadow display.
    candidates.extend(["com.quark.browser", "com.android.chrome"])
    for package in candidates:
        if package and package_installed(adb, package):
            return package
    raise PackageResolutionError("no configured or known browser package is installed")


def resolve_notes_package(config: Config, adb: AdbClient) -> str:
    candidates = [config.notes_package] if config.notes_package else []
    candidates.extend(["com.huawei.notepad", "com.google.android.keep"])
    for package in candidates:
        if package and package_installed(adb, package):
            return package
    raise PackageResolutionError("no configured or known notes package is installed")


def resolve_goal_package(goal: str, config: Config, adb: AdbClient) -> tuple[str, dict[str, str]]:
    known = {
        "美团": "com.sankuai.meituan",
        "浏览器": resolve_browser_package(config, adb),
        "网页": resolve_browser_package(config, adb),
    }
    installed = {name: package for name, package in known.items() if package_installed(adb, package)}
    for name, package in installed.items():
        if name in goal:
            return package, installed
    raise PackageResolutionError(f"任务未指定已支持的应用；当前可用：{', '.join(installed) or '无'}")
