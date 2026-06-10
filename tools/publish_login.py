from __future__ import annotations


def wait_for_panel_login(
    *,
    platform_label: str,
    confirm_kind: str = "y",
    hint: str = "",
) -> None:
    """输出 web_panel LOGIN_WAIT_PATTERNS 可识别的行，再阻塞等待面板确认后写入 stdin。"""
    if hint.strip():
        print(hint.strip(), flush=True)
    if confirm_kind == "y":
        print("登录完成后请输入 y 继续", flush=True)
        prompt = "登录完成后请输入 y 继续："
    else:
        print("完成扫码登录后按回车继续", flush=True)
        prompt = "完成扫码登录后按回车继续："
    try:
        if confirm_kind == "y":
            answer = input(prompt).strip().lower()
            if answer != "y":
                print("请输入 y 继续。", flush=True)
                wait_for_panel_login(platform_label=platform_label, confirm_kind="y", hint="")
                return
        else:
            input(prompt)
    except EOFError as exc:
        raise RuntimeError(
            f"{platform_label} 尚未登录，且当前为非交互环境，无法等待手动登录。"
        ) from exc
