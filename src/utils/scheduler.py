"""Scheduler for periodic tasks like reminders."""

import os
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from slack_sdk import WebClient

from ..services.spreadsheet import spreadsheet_service
from ..services.chatwork import chatwork_service


class Scheduler:
    """定期タスクのスケジューラー"""

    def __init__(self, slack_client: WebClient):
        self.slack_client = slack_client
        self.notification_channel = os.getenv("SLACK_NOTIFICATION_CHANNEL", "#accounting")
        self.scheduler = BackgroundScheduler()

    def start(self):
        """スケジューラーを開始"""
        # 月初の月曜日にリマインドを送信
        # 毎週月曜日の朝9時にチェックし、その月の最初の月曜日なら通知
        self.scheduler.add_job(
            self._check_and_send_monthly_reminder,
            CronTrigger(day_of_week="mon", hour=9, minute=0),
            id="monthly_reminder",
            name="Monthly accounting reminder"
        )

        # 毎日朝10時に請求書取得をチェック（オプション）
        self.scheduler.add_job(
            self._daily_invoice_check,
            CronTrigger(hour=10, minute=0),
            id="daily_invoice_check",
            name="Daily invoice check"
        )

        self.scheduler.start()
        print("Scheduler started")

    def stop(self):
        """スケジューラーを停止"""
        self.scheduler.shutdown()
        print("Scheduler stopped")

    def _check_and_send_monthly_reminder(self):
        """月初の月曜日かチェックしてリマインドを送信"""
        today = datetime.now()

        # その月の最初の月曜日かチェック
        first_day = today.replace(day=1)
        days_until_monday = (7 - first_day.weekday()) % 7
        if first_day.weekday() == 0:  # 1日が月曜日の場合
            first_monday = first_day
        else:
            first_monday = first_day + timedelta(days=days_until_monday)

        if today.date() == first_monday.date():
            self._send_monthly_reminder()

    def _send_monthly_reminder(self):
        """月次リマインドを送信"""
        try:
            period = spreadsheet_service.get_current_period()
            subscriptions = spreadsheet_service.get_subscriptions(active_only=True)

            # 今月の予定支出を計算
            monthly_total = sum(
                s.amount for s in subscriptions
                if s.billing_cycle.value == "monthly"
            )

            blocks = [
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": f"📅 {period} 経理作業リマインド"}
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"今月の経理作業を開始しましょう！\n\n"
                            f"*📋 登録サブスク:* {len(subscriptions)}件\n"
                            f"*💰 予定支出:* ¥{monthly_total:,.0f}\n"
                        )
                    }
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            "*やることリスト:*\n"
                            "1. カード明細CSVをダウンロードしてアップロード\n"
                            "2. 銀行明細CSVをダウンロードしてアップロード\n"
                            "3. 不足請求書を準備\n"
                            "4. 照会結果を確認"
                        )
                    }
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "📊 状況を確認"},
                            "action_id": "check_status_from_reminder"
                        }
                    ]
                }
            ]

            self.slack_client.chat_postMessage(
                channel=self.notification_channel,
                text=f"📅 {period} 経理作業リマインド",
                blocks=blocks
            )

            # Chatworkにも月次リマインドを送信
            chatwork_service.notify_monthly_reminder(
                period=period,
                subscription_count=len(subscriptions),
                monthly_total=monthly_total,
            )

            print(f"Monthly reminder sent for {period}")

        except Exception as e:
            print(f"Error sending monthly reminder: {e}")

    def _daily_invoice_check(self):
        """日次の請求書チェック（メール取得など）"""
        try:
            # この機能は将来的に実装
            # 現時点ではログのみ
            print(f"Daily invoice check at {datetime.now()}")
        except Exception as e:
            print(f"Error in daily invoice check: {e}")

    def send_missing_invoices_reminder(self, missing_count: int, period: str):
        """不足請求書のリマインドを送信"""
        try:
            self.slack_client.chat_postMessage(
                channel=self.notification_channel,
                text=(
                    f"⚠️ *{period}* の照会で {missing_count}件 の請求書が不足しています。\n"
                    f"`/accounting-status` で詳細を確認してください。"
                )
            )
        except Exception as e:
            print(f"Error sending missing invoices reminder: {e}")


# 注: schedulerインスタンスはapp.pyで作成する
