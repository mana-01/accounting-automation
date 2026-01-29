"""Main application entry point."""

import os
import asyncio
from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from .handlers import register_commands, register_events, register_actions
from .services.spreadsheet import spreadsheet_service
from .utils.scheduler import Scheduler

# 環境変数を読み込み
load_dotenv()


def create_app() -> App:
    """Slack Boltアプリを作成"""
    app = App(
        token=os.getenv("SLACK_BOT_TOKEN"),
        signing_secret=os.getenv("SLACK_SIGNING_SECRET"),
    )

    # ハンドラーを登録
    register_commands(app)
    register_events(app)
    register_actions(app)

    # リマインドボタンのアクション
    @app.action("check_status_from_reminder")
    def handle_check_status_from_reminder(ack, body, client):
        ack()
        user_id = body["user"]["id"]

        try:
            period = spreadsheet_service.get_current_period()
            invoices = spreadsheet_service.get_invoices(period=period)
            subscriptions = spreadsheet_service.get_subscriptions(active_only=True)

            matched_count = len([i for i in invoices if i.status.value == "matched"])
            pending_count = len([i for i in invoices if i.status.value == "pending"])

            client.chat_postEphemeral(
                channel=body["channel"]["id"],
                user=user_id,
                text=(
                    f"*📊 {period} の状況*\n"
                    f"• 登録サブスク: {len(subscriptions)}件\n"
                    f"• 取得済み請求書: {len(invoices)}件\n"
                    f"  - ✅ 照会済み: {matched_count}件\n"
                    f"  - ⏳ 未照会: {pending_count}件\n\n"
                    f"詳細は `/accounting-status` で確認できます。"
                )
            )
        except Exception as e:
            client.chat_postEphemeral(
                channel=body["channel"]["id"],
                user=user_id,
                text=f"❌ エラーが発生しました: {str(e)}"
            )

    return app


def main():
    """アプリケーションを起動"""
    print("🚀 Starting Accounting Automation Bot...")

    # アプリを作成
    app = create_app()

    # スプレッドシートを初期化
    try:
        print("📊 Initializing spreadsheet...")
        asyncio.get_event_loop().run_until_complete(spreadsheet_service.initialize())
    except Exception as e:
        print(f"⚠️ Spreadsheet initialization warning: {e}")
        print("  (This is OK if sheets already exist)")

    # スケジューラーを開始
    scheduler = Scheduler(app.client)
    scheduler.start()

    # Socket Modeで起動
    handler = SocketModeHandler(
        app,
        os.getenv("SLACK_APP_TOKEN")
    )

    print("✅ Bot is ready!")
    print("   - Use /accounting-help to see available commands")
    print("   - Upload CSV files to start reconciliation")

    try:
        handler.start()
    except KeyboardInterrupt:
        print("\n🛑 Shutting down...")
        scheduler.stop()


if __name__ == "__main__":
    main()
