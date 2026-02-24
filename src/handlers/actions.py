"""Slack interactive action handlers."""

import os
import requests
from datetime import datetime
from slack_bolt import App

from ..models import Subscription, Invoice, BillingCycle, PaymentMethod, FetchMethod, FileNamingRule, InvoiceStatus
from ..services.spreadsheet import spreadsheet_service
from ..services.drive import drive_service


def register_actions(app: App):
    """アクションハンドラーを登録"""

    @app.view("add_subscription_modal")
    def handle_add_subscription_submission(ack, body, client, view, logger):
        """サブスク登録モーダルの送信処理"""
        ack()

        try:
            values = view["state"]["values"]

            # 値を取得
            name = values["name_block"]["name_input"]["value"]
            vendor = values["vendor_block"]["vendor_input"]["value"]
            amount_str = values["amount_block"]["amount_input"]["value"]
            cycle = values["cycle_block"]["cycle_select"]["selected_option"]["value"]
            payment = values["payment_block"]["payment_select"]["selected_option"]["value"]
            fetch = values["fetch_block"]["fetch_select"]["selected_option"]["value"]
            file_naming = values["file_naming_block"]["file_naming_select"]["selected_option"]["value"]
            email_subject = values["email_subject_block"]["email_subject_input"].get("value")

            # 金額をパース
            try:
                amount = float(amount_str.replace(",", "").replace("¥", "").replace("円", ""))
            except ValueError:
                # エラーメッセージを送信
                user_id = body["user"]["id"]
                client.chat_postMessage(
                    channel=user_id,
                    text=f"❌ 金額の形式が正しくありません: {amount_str}"
                )
                return

            # サブスクを作成
            subscription = Subscription(
                name=name,
                vendor=vendor,
                amount=amount,
                billing_cycle=BillingCycle(cycle),
                payment_method=PaymentMethod(payment),
                fetch_method=FetchMethod(fetch),
                file_naming=FileNamingRule(file_naming),
                email_subject=email_subject,
            )

            # 保存
            spreadsheet_service.add_subscription(subscription)

            # 確認メッセージを送信
            user_id = body["user"]["id"]
            file_naming_label = "Rename (日付_ベンダー_金額)" if file_naming == "rename" else "Original (元のファイル名)"
            client.chat_postMessage(
                channel=user_id,
                text=(
                    f"✅ サブスクリプションを登録しました！\n"
                    f"• *{name}* ({vendor})\n"
                    f"• 💰 ¥{amount:,.0f} / {cycle}\n"
                    f"• 取得方法: {fetch}\n"
                    f"• ファイル命名: {file_naming_label}"
                )
            )

        except Exception as e:
            logger.error(f"Error adding subscription: {e}")
            user_id = body["user"]["id"]
            client.chat_postMessage(
                channel=user_id,
                text=f"❌ サブスクの登録に失敗しました: {str(e)}"
            )

    @app.action("save_invoice_pdf")
    def handle_save_invoice_pdf(ack, body, client, action, say, logger):
        """PDFを請求書として保存"""
        ack()

        file_id = action["value"]
        channel_id = body["channel"]["id"]
        user_id = body["user"]["id"]

        try:
            # ファイル情報を取得
            file_info = client.files_info(file=file_id)
            file_data = file_info["file"]
            file_name = file_data.get("name", "invoice.pdf")

            # サブスク選択モーダルを表示
            subscriptions = spreadsheet_service.get_subscriptions(active_only=True)

            options = [
                {
                    "text": {"type": "plain_text", "text": f"{s.name} ({s.vendor})"},
                    "value": s.id
                }
                for s in subscriptions
            ]

            # 「その他」オプションを追加
            options.append({
                "text": {"type": "plain_text", "text": "その他（新規）"},
                "value": "new"
            })

            client.views_open(
                trigger_id=body["trigger_id"],
                view={
                    "type": "modal",
                    "callback_id": "save_invoice_modal",
                    "private_metadata": file_id,
                    "title": {"type": "plain_text", "text": "請求書を保存"},
                    "submit": {"type": "plain_text", "text": "保存"},
                    "close": {"type": "plain_text", "text": "キャンセル"},
                    "blocks": [
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f"📄 *{file_name}* を請求書として保存します。"
                            }
                        },
                        {
                            "type": "input",
                            "block_id": "subscription_block",
                            "label": {"type": "plain_text", "text": "サブスクリプション"},
                            "element": {
                                "type": "static_select",
                                "action_id": "subscription_select",
                                "options": options
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
                            "block_id": "date_block",
                            "label": {"type": "plain_text", "text": "請求日"},
                            "element": {
                                "type": "datepicker",
                                "action_id": "date_picker",
                                "initial_date": datetime.now().strftime("%Y-%m-%d")
                            }
                        }
                    ]
                }
            )

        except Exception as e:
            logger.error(f"Error opening save invoice modal: {e}")
            say(channel=channel_id, text=f"❌ エラーが発生しました: {str(e)}")

    @app.view("save_invoice_modal")
    def handle_save_invoice_modal_submission(ack, body, client, view, logger):
        """請求書保存モーダルの送信処理"""
        ack()

        file_id = view["private_metadata"]
        user_id = body["user"]["id"]

        try:
            values = view["state"]["values"]

            subscription_id = values["subscription_block"]["subscription_select"]["selected_option"]["value"]
            amount_str = values["amount_block"]["amount_input"]["value"]
            invoice_date = values["date_block"]["date_picker"]["selected_date"]

            # 金額をパース
            amount = float(amount_str.replace(",", "").replace("¥", "").replace("円", ""))

            # サブスク情報を取得
            if subscription_id == "new":
                subscription_name = "未分類"
            else:
                subscription = spreadsheet_service.get_subscription_by_id(subscription_id)
                subscription_name = subscription.name if subscription else "不明"

            # ファイルをダウンロード
            file_info = client.files_info(file=file_id)
            file_data = file_info["file"]
            file_name = file_data.get("name", "invoice.pdf")
            download_url = file_data.get("url_private_download")

            headers = {"Authorization": f"Bearer {os.getenv('SLACK_BOT_TOKEN')}"}
            response = requests.get(download_url, headers=headers)

            if response.status_code != 200:
                raise Exception("ファイルのダウンロードに失敗しました")

            # Google Driveにアップロード
            period = f"{datetime.strptime(invoice_date, '%Y-%m-%d').year}年{datetime.strptime(invoice_date, '%Y-%m-%d').month}月"
            upload_result = drive_service.upload_invoice(
                file_content=response.content,
                file_name=f"{subscription_name}_{invoice_date}_{file_name}",
                mime_type="application/pdf",
                period=period
            )

            # 請求書レコードを作成
            invoice = Invoice(
                subscription_id=subscription_id if subscription_id != "new" else "",
                subscription_name=subscription_name,
                amount=amount,
                invoice_date=invoice_date,
                drive_file_id=upload_result["file_id"],
                drive_url=upload_result["web_view_link"],
                source_type="manual",
                status=InvoiceStatus.PENDING,
            )

            spreadsheet_service.add_invoice(invoice)

            # 確認メッセージ
            client.chat_postMessage(
                channel=user_id,
                text=(
                    f"✅ 請求書を保存しました！\n"
                    f"• *{subscription_name}*\n"
                    f"• 📅 {invoice_date}\n"
                    f"• 💰 ¥{amount:,.0f}\n"
                    f"• 📁 <{upload_result['web_view_link']}|Google Driveで表示>"
                )
            )

        except Exception as e:
            logger.error(f"Error saving invoice: {e}")
            client.chat_postMessage(
                channel=user_id,
                text=f"❌ 請求書の保存に失敗しました: {str(e)}"
            )

    @app.action("skip_file")
    def handle_skip_file(ack, body, client):
        """ファイルをスキップ"""
        ack()

        # メッセージを更新してボタンを削除
        client.chat_update(
            channel=body["channel"]["id"],
            ts=body["message"]["ts"],
            text="ファイルをスキップしました。",
            blocks=[]
        )
