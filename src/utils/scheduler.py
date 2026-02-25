"""Scheduler for periodic tasks like reminders."""

import os
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from slack_sdk import WebClient

from ..services.spreadsheet import spreadsheet_service


class Scheduler:
    """定期タスクのスケジューラー"""

    def __init__(self, slack_client: WebClient):
        self.slack_client = slack_client
        self.notification_channel = os.getenv("SLACK_NOTIFICATION_CHANNEL", "#accounting")
        self.scheduler = BackgroundScheduler()

    def start(self):
        """スケジューラーを開始"""
        # 毎月5日の15時にリマインドを送信
        self.scheduler.add_job(
            self._send_monthly_reminder,
            CronTrigger(day=5, hour=15, minute=0),
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

        # 毎月1日の朝9時にハロートランク請求書を自動生成（前月分）
        self.scheduler.add_job(
            self._generate_hellotrunk_invoice,
            CronTrigger(day=1, hour=9, minute=0),
            id="hellotrunk_invoice",
            name="Monthly HelloTrunk invoice generation"
        )

        # 毎月1日の朝9時にメールから請求書PDFを自動取得（前月分）
        self.scheduler.add_job(
            self._fetch_email_invoices,
            CronTrigger(day=1, hour=9, minute=30),
            id="email_invoice_fetch",
            name="Monthly email invoice fetch"
        )

        self.scheduler.start()
        print("Scheduler started")

    def stop(self):
        """スケジューラーを停止"""
        self.scheduler.shutdown()
        print("Scheduler stopped")

    def _send_monthly_reminder(self):
        """月次リマインドを送信"""
        try:
            period = spreadsheet_service.get_current_period()

            # ②手動取得項目と③固定スキャンをシートから取得
            manual_items = spreadsheet_service.get_reminder_items(active_only=True, category="manual")
            fixed_scan_items = spreadsheet_service.get_reminder_items(active_only=True, category="fixed_scan")

            # ②手動取得項目のテキスト構築
            manual_lines = []
            for item in manual_items:
                line = f"• {item.name}"
                if item.notes:
                    line += f"（{item.notes}）"
                if item.url:
                    line = f"• <{item.url}|{item.name}>"
                    if item.notes:
                        line += f"（{item.notes}）"
                manual_lines.append(line)
            manual_text = "\n".join(manual_lines) if manual_lines else "• _項目なし_"

            # ③固定スキャンのテキスト構築
            fixed_lines = []
            for item in fixed_scan_items:
                line = f"• {item.name}"
                if item.notes:
                    line += f"（{item.notes}）"
                fixed_lines.append(line)
            fixed_text = "\n".join(fixed_lines) if fixed_lines else "• _項目なし_"

            blocks = [
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": f"\U0001f4c5 {period} \u7d4c\u7406\u4f5c\u696d\u306f\u3058\u3081\u308b\u3088\uff01\U0001f31e"}
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "今月の経理作業を開始しましょう！\nまず以下の準備をお願いします。"
                    }
                },
                {"type": "divider"},
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            "*① 参照元データ取得*\n"
                            "それぞれの明細を取得してきてください\n"
                            "• <https://www.saisoncard.co.jp/|クレジットカード（セゾンカード）>\n"
                            "• <https://sso.gmo-aozora.com/corp/b2c/login|銀行（GMOあおぞらネット銀行）>"
                        )
                    }
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*② 手動取得項目*\n{manual_text}"
                    }
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*③ 固定スキャン*\n{fixed_text}"
                    }
                },
                {"type": "divider"},
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            "*流れ*\n"
                            "☑︎ CSVを2つアップロード\n"
                            "☑︎ `/accounting-reconcile` で足りていないものを確認\n"
                            "☑︎ 不足分をSlackにアップロード\n"
                            "☑︎ `/accounting-reconcile` で再確認\n"
                            "☑︎ すべて揃ったら `/accounting-share` でエナリさんに共有"
                        )
                    }
                },
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": "②③の項目は `/accounting-subscriptions` で編集できます"
                        }
                    ]
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
                text=f"\U0001f4c5 {period} \u7d4c\u7406\u4f5c\u696d\u306f\u3058\u3081\u308b\u3088\uff01\U0001f31e",
                blocks=blocks
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

    def _generate_hellotrunk_invoice(self):
        """ハロートランク請求書を自動生成してDriveにアップロード（毎月1日実行・前月分）"""
        try:
            from api.services.hellotrunk_invoice import generate_and_upload

            result = generate_and_upload()
            period = f"{result['year']}年{result['month']}月"

            if result.get("skipped"):
                print(f"[hellotrunk] {period} - already exists, skipped")
                return

            print(f"[hellotrunk] {period} - generated: {result['filename']}")

            # Slackに通知
            self.slack_client.chat_postMessage(
                channel=self.notification_channel,
                text=(
                    f"*ハロートランク請求書を自動生成しました*\n"
                    f"対象月: {period}\n"
                    f"ファイル名: `{result['filename']}`\n"
                    f"<{result.get('web_view_link', '')}|Google Driveで表示>"
                ),
            )
        except Exception as e:
            print(f"Error generating HelloTrunk invoice: {e}")
            try:
                self.slack_client.chat_postMessage(
                    channel=self.notification_channel,
                    text=f"ハロートランク請求書の自動生成に失敗しました: {e}",
                )
            except Exception:
                pass

    def _fetch_email_invoices(self):
        """メールから請求書PDFを自動取得してDriveに保存（毎月1日実行・前月分）"""
        try:
            from api.services.invoice_fetcher import invoice_fetcher

            # 前月の期間コードを算出
            today = datetime.now()
            if today.month == 1:
                target_year = today.year - 1
                target_month = 12
            else:
                target_year = today.year
                target_month = today.month - 1
            period_code = f"{target_year}{target_month:02d}"
            period = f"{target_year}年{target_month}月"

            results = invoice_fetcher.fetch_invoices_by_period(period_code)

            saved = results.get("saved", 0)
            registered = results.get("registered", 0)
            skipped = results.get("skipped", 0)
            errors = results.get("errors", []) + results.get("register_errors", [])

            if saved > 0 or errors:
                summary_parts = [
                    f"*メール請求書の自動取得が完了しました*",
                    f"対象月: {period}",
                    f"• Drive保存: {saved}件",
                    f"• シート登録: {registered}件",
                ]
                if skipped > 0:
                    summary_parts.append(f"• スキップ（重複）: {skipped}件")
                if errors:
                    summary_parts.append(f"• エラー: {len(errors)}件")

                self.slack_client.chat_postMessage(
                    channel=self.notification_channel,
                    text="\n".join(summary_parts),
                )
            else:
                print(f"[email_invoice_fetch] {period} - no new invoices found")

        except Exception as e:
            print(f"Error fetching email invoices: {e}")
            try:
                self.slack_client.chat_postMessage(
                    channel=self.notification_channel,
                    text=f"メール請求書の自動取得に失敗しました: {e}",
                )
            except Exception:
                pass

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
