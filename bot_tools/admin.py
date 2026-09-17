"""Server-only invitation and final confirmation for private superadmin binding."""
import argparse
import sys

from .storage import Store, ToolError


def main():
    parser = argparse.ArgumentParser(description="仅服务器主人使用：总管理员授权 / Server-only superadmin authorization")
    parser.add_argument("action",choices=["token","add","remove","list"])
    parser.add_argument("code",nargs="?")
    args = parser.parse_args()
    store = Store()
    if args.action in {"token", "list"} and args.code:
        parser.error("This action takes no identity code / 此操作不需要身份码")
    if args.action == "token":
        token = store.create_admin_token()
        print("一次性授权码 / One-time invitation (10 minutes, single use):")
        print(token)
        print("仅在你自己的机器人私聊发送 / Send only in your own bot private chat:")
        print(f"/bot whoami {token}")
        print("随后在本机执行 admin add 身份码；15 分钟内确认，兑换不会直接授权。")
        print("Then confirm with admin add CODE on this server within 15 minutes.")
        print("重新生成会作废上一张未兑换的授权码。不要公开或转发。")
        return
    if args.action == "list":
        with store.connect() as db:
            for r in db.execute("SELECT code,role,private FROM identities WHERE role!='member'"):
                role = "superadmin / 总管理员" if r["role"] == "owner" else "delegated admin / 手动委派管理员"
                print(f"{r['code']}  {role}  {'private / 私聊' if r['private'] else 'group/channel / 群或频道'}")
        return
    if not args.code:
        parser.error("Missing identity code / 请先在服务器执行 admin token，再用私聊兑换的身份码确认")
    try:
        store.grant(args.code,"owner" if args.action == "add" else "member")
    except ToolError as exc:
        print(str(exc),file=sys.stderr)
        raise SystemExit(1) from None
    print("权限已更新 / Authorization updated. No restart needed.")


if __name__ == "__main__":
    main()
