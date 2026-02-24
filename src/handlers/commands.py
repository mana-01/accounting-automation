"""Slack slash command handlers."""

from slack_bolt import App
from datetime import datetime

from ..models import PaymentMethod
from ..services.spreadsheet import spreadsheet_service
from ..services.reconciliation import reconciliation_service
from ..services.drive import drive_service


def register_commands(app: App):
    """スラッシュコマンドを登録"""

    @app.command("/accounting-help")
    def handle_help(ack, respond):
        """ヘルプコマンド"""
        ack()
        respond({
            "response_type": "ephemeral",
            "blocks": [
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": "📊 経理自動化ボット ヘルプ"}
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "*利用可能なコマンド:*"
                    }
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            "• `/accounting-help` - このヘルプを表示\n"
                            "• `/accounting-status` - 今月の照会状況を確認\n"
                            "• `/accounting-subscriptions` - サブスク一覧を表示\n"
                            "• `/accounting-add-subscription` - 新規サブスクを登録\n"
                            "• `/accounting-fetch-invoices` - メールから請求書を取得\n"
                            "• `/accounting-invoices [期間]` - 請求書一覧を表示\n"
                            "• `/accounting-share [期間]` - 税理士さんに請求書を共有"
                        )
                    }
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            "*CSV明細のアップロード:*\n"
                            "このチャンネルにカード明細や銀行明細のCSVファイルを"
                            "アップロードすると、自動で照会処理を行います。"
                        )
                    }
                }
            ]
        })

    @app.command("/accounting-status")
    def handle_status(ack, respond):
        """今月の照会状況を確認"""
        ack()

        try:
            period = spreadsheet_service.get_current_period()
            invoices = spreadsheet_service.get_invoices(period=period)
            subscriptions = spreadsheet_service.get_subscriptions(active_only=True)
            history = spreadsheet_service.get_reconciliation_history()

            # 今月の照会履歴を検索
            current_reconciliation = None
            for h in history:
                if h.period == period:
                    current_reconciliation = h
                    break

            matched_count = len([i for i in invoices if i.status.value == "matched"])
            pending_count = len([i for i in invoices if i.status.value == "pending"])

            status_text = (
                f"*📅 期間:* {period}\n"
                f"*📋 登録サブスク数:* {len(subscriptions)}件\n"
                f"*📄 取得済み請求書:* {len(invoices)}件\n"
                f"  • ✅ 照会済み: {matched_count}件\n"
                f"  • ⏳ 未照会: {pending_count}件\n"
            )

            if current_reconciliation:
                status_text += (
                    f"\n*🔍 最終照会:*\n"
                    f"  • 日時: {current_reconciliation.reconciliation_date}\n"
                    f"  • 取引数: {current_reconciliation.total_transactions}件\n"
                    f"  • マッチ: {current_reconciliation.matched_count}件\n"
                    f"  • 不足: {current_reconciliation.unmatched_count}件"
                )

            respond({
                "response_type": "ephemeral",
                "blocks": [
                    {
                        "type": "header",
                        "text": {"type": "plain_text", "text": "📊 経理状況"}
                    },
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": status_text}
                    }
                ]
            })
        except Exception as e:
            respond(f"エラーが発生しました: {str(e)}")

    @app.command("/accounting-subscriptions")
    def handle_subscriptions(ack, respond):
        """サブスク一覧を表示"""
        ack()

        try:
            subscriptions = spreadsheet_service.get_subscriptions()

            if not subscriptions:
                respond("登録されているサブスクリプションはありません。")
                return

            blocks = [
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": "📋 サブスクリプション一覧"}
                }
            ]

            for sub in subscriptions[:20]:  # 最大20件
                status_emoji = "✅" if sub.is_active else "⏸️"
                fetch_emoji = {
                    "email_pdf": "📧",
                    "email_link": "🔗",
                    "login": "🔐",
                    "manual": "✋"
                }.get(sub.fetch_method.value, "❓")

                blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"{status_emoji} *{sub.name}* ({sub.vendor})\n"
                            f"💰 ¥{sub.amount:,.0f} / {sub.billing_cycle.value}\n"
                            f"{fetch_emoji} 取得方法: {sub.fetch_method.value}"
                        )
                    }
                })

            if len(subscriptions) > 20:
                blocks.append({
                    "type": "context",
                    "elements": [
                        {"type": "mrkdwn", "text": f"他 {len(subscriptions) - 20} 件..."}
                    ]
                })

            respond({"response_type": "ephemeral", "blocks": blocks})
        except Exception as e:
            respond(f"エラーが発生しました: {str(e)}")

    @app.command("/accounting-add-subscription")
    def handle_add_subscription(ack, client, body):
        """新規サブスク登録モーダルを表示"""
        ack()

        client.views_open(
            trigger_id=body["trigger_id"],
            view={
                "type": "modal",
                "callback_id": "add_subscription_modal",
                "title": {"type": "plain_text", "text": "サブスク登録"},
                "submit": {"type": "plain_text", "text": "登録"},
                "close": {"type": "plain_text", "text": "キャンセル"},
                "blocks": [
                    {
                        "type": "input",
                        "block_id": "name_block",
                        "label": {"type": "plain_text", "text": "サービス名"},
                        "element": {
                            "type": "plain_text_input",
                            "action_id": "name_input",
                            "placeholder": {"type": "plain_text", "text": "例: AWS"}
                        }
                    },
                    {
                        "type": "input",
                        "block_id": "vendor_block",
                        "label": {"type": "plain_text", "text": "ベンダー名"},
                        "element": {
                            "type": "plain_text_input",
                            "action_id": "vendor_input",
                            "placeholder": {"type": "plain_text", "text": "例: Amazon Web Services"}
                        }
                    },
                    {
                        "type": "input",
                        "block_id": "amount_block",
                        "label": {"type": "plain_text", "text": "金額 (円)"},
                        "element": {
                            "type": "plain_text_input",
                            "action_id": "amount_input",
                            "placeholder": {"type": "plain_text", "text": "例: 10000"}
                        }
                    },
                    {
                        "type": "input",
                        "block_id": "cycle_block",
                        "label": {"type": "plain_text", "text": "請求サイクル"},
                        "element": {
                            "type": "static_select",
                            "action_id": "cycle_select",
                            "options": [
                                {"text": {"type": "plain_text", "text": "月次"}, "value": "monthly"},
                                {"text": {"type": "plain_text", "text": "年次"}, "value": "yearly"},
                                {"text": {"type": "plain_text", "text": "四半期"}, "value": "quarterly"},
                            ]
                        }
                    },
                    {
                        "type": "input",
                        "block_id": "payment_block",
                        "label": {"type": "plain_text", "text": "支払方法"},
                        "element": {
                            "type": "static_select",
                            "action_id": "payment_select",
                            "options": [
                                {"text": {"type": "plain_text", "text": "カード"}, "value": "card"},
                                {"text": {"type": "plain_text", "text": "銀行振込"}, "value": "bank"},
                            ]
                        }
                    },
                    {
                        "type": "input",
                        "block_id": "fetch_block",
                        "label": {"type": "plain_text", "text": "請求書取得方法"},
                        "element": {
                            "type": "static_select",
                            "action_id": "fetch_select",
                            "options": [
                                {"text": {"type": "plain_text", "text": "メール (PDF添付)"}, "value": "email_pdf"},
                                {"text": {"type": "plain_text", "text": "メール (リンク)"}, "value": "email_link"},
                                {"text": {"type": "plain_text", "text": "ログイン取得"}, "value": "login"},
                                {"text": {"type": "plain_text", "text": "手動"}, "value": "manual"},
                            ]
                        }
                    },
                    {
                        "type": "input",
                        "block_id": "email_subject_block",
                        "label": {"type": "plain_text", "text": "メール件名パターン (オプション)"},
                        "optional": True,
                        "element": {
                            "type": "plain_text_input",
                            "action_id": "email_subject_input",
                            "placeholder": {"type": "plain_text", "text": "例: AWS請求書"}
                        }
                    }
                ]
            }
        )

    @app.command("/accounting-invoices")
    def handle_invoices(ack, respond, command):
        """請求書一覧を表示"""
        ack()

        try:
            # 期間を取得（引数があれば使用、なければ今月）
            period = command.get("text", "").strip()
            if not period:
                period = spreadsheet_service.get_current_period()

            invoices = spreadsheet_service.get_invoices(period=period)

            if not invoices:
                respond(f"📄 {period} の請求書はまだありません。")
                return

            blocks = [
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": f"📄 請求書一覧 ({period})"}
                }
            ]

            for inv in invoices[:15]:
                status_emoji = {
                    "matched": "✅",
                    "pending": "⏳",
                    "missing": "❌"
                }.get(inv.status.value, "❓")

                link_text = ""
                if inv.drive_url:
                    link_text = f" <{inv.drive_url}|📎 表示>"

                blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"{status_emoji} *{inv.subscription_name}*\n"
                            f"📅 {inv.invoice_date} | 💰 ¥{inv.amount:,.0f}{link_text}"
                        )
                    }
                })

            respond({"response_type": "ephemeral", "blocks": blocks})
        except Exception as e:
            respond(f"エラーが発生しました: {str(e)}")

    @app.command("/accounting-fetch-invoices")
    def handle_fetch_invoices(ack, respond, client):
        """メールから請求書を取得"""
        ack()
        respond("📧 メールから請求書を取得しています... (この処理は数分かかる場合があります)")

        # 実際の取得処理は非同期で行うべき
        # ここでは簡易的にメッセージのみ返す
        # TODO: 実際のGmail取得処理を実装

    @app.command("/accounting-share")
    def handle_share(ack, respond, command):
        """税理士さんにファイルを共有"""
        ack()

        try:
            # 期間を取得（引数があれば使用、なければ今月）
            period = command.get("text", "").strip()
            if not period:
                period = spreadsheet_service.get_current_period()

            # 照会状況を確認
            history = spreadsheet_service.get_reconciliation_history()
            current_reconciliation = None
            for h in history:
                if h.period == period:
                    current_reconciliation = h
                    break

            if not current_reconciliation:
                respond(f"⚠️ {period} の照会がまだ実行されていません。先にCSVファイルをアップロードして照会を行ってください。")
                return

            if current_reconciliation.unmatched_count > 0:
                respond(
                    f"⚠️ {period} にはまだ {current_reconciliation.unmatched_count}件の不足請求書があります。\n"
                    f"すべての照会が一致してから共有してください。"
                )
                return

            respond(f"📤 {period} のファイルを税理士さんの共有フォルダにコピーしています...")

            # 請求書とサブスク情報を取得
            invoices = spreadsheet_service.get_invoices(period=period)
            subscriptions = spreadsheet_service.get_subscriptions()

            sub_payment_map = {}
            for sub in subscriptions:
                sub_payment_map[sub.id] = sub.payment_method

            card_file_ids = []
            bank_file_ids = []

            for inv in invoices:
                if not inv.drive_file_id:
                    continue

                payment_method = sub_payment_map.get(inv.subscription_id)
                if payment_method == PaymentMethod.BANK:
                    bank_file_ids.append(inv.drive_file_id)
                else:
                    card_file_ids.append(inv.drive_file_id)

            share_result = drive_service.share_with_accountant(
                period=period,
                card_file_ids=card_file_ids,
                bank_file_ids=bank_file_ids
            )

            result_lines = [f"✅ *{period}* のファイルを税理士さんの共有フォルダにコピーしました！\n"]

            if share_result["card_count"] > 0:
                result_lines.append(
                    f"• 💳 クレジットカード: {share_result['card_count']}件 "
                    f"<{share_result['card_folder_url']}|フォルダを開く>"
                )

            if share_result["bank_count"] > 0:
                result_lines.append(
                    f"• 🏦 銀行: {share_result['bank_count']}件 "
                    f"<{share_result['bank_folder_url']}|フォルダを開く>"
                )

            if share_result["card_count"] == 0 and share_result["bank_count"] == 0:
                result_lines.append("⚠️ コピー対象のファイルがありませんでした。")

            respond({
                "response_type": "in_channel",
                "blocks": [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": "\n".join(result_lines)
                        }
                    }
                ]
            })

        except Exception as e:
            respond(f"❌ 税理士共有でエラーが発生しました: {str(e)}")
