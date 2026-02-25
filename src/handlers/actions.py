"""Slack interactive action handlers."""

import json
import os
import requests
from datetime import datetime
from slack_bolt import App

from ..models import Subscription, Invoice, EmailRule, EmailRuleCategory, BillingCycle, PaymentMethod, FetchMethod, FileNamingRule, InvoiceStatus
from ..services.spreadsheet import spreadsheet_service
from ..services.drive import drive_service
from ..services.reconciliation import reconciliation_service
from .commands import _build_add_rule_modal


def register_actions(app: App):
    """アクションハンドラーを登録"""

    @app.action("rule_category_select")
    def handle_rule_category_select(ack, body, client):
        """取得ルール登録モーダルのカテゴリ変更時にフォームを動的に更新"""
        ack()

        selected_category = body["actions"][0]["selected_option"]["value"]
        view_id = body["view"]["id"]

        client.views_update(
            view_id=view_id,
            view=_build_add_rule_modal(selected_category)
        )

    @app.view("add_email_rule_modal")
    def handle_add_email_rule_submission(ack, body, client, view, logger):
        """取得ルール登録モーダルの送信処理"""
        ack()

        try:
            values = view["state"]["values"]

            # 共通フィールド
            category_value = values["category_block"]["rule_category_select"]["selected_option"]["value"]
            name = values["name_block"]["name_input"]["value"]
            category = EmailRuleCategory(category_value)

            # カテゴリ別フィールドを取得
            rule = EmailRule(name=name, category=category)

            if category_value == "email":
                rule.sender_email = values["sender_email_block"]["sender_email_input"]["value"]
                rule.subject_pattern = values["subject_block"]["subject_input"]["value"]
                rule.fetch_type = values["fetch_type_block"]["fetch_type_select"]["selected_option"]["value"]
            elif category_value == "manual":
                url_val = values.get("url_block", {}).get("url_input", {}).get("value")
                notes_val = values.get("notes_block", {}).get("notes_input", {}).get("value")
                rule.url = url_val or ""
                rule.notes = notes_val or ""
            elif category_value == "scan":
                notes_val = values.get("notes_block", {}).get("notes_input", {}).get("value")
                rule.notes = notes_val or ""

            # 保存
            spreadsheet_service.add_email_rule(rule)

            # 確認メッセージを送信
            user_id = body["user"]["id"]
            category_labels = {
                "email": "📧 メールで自動取得",
                "manual": "✋ 手動確認",
                "scan": "📄 スキャン",
            }
            detail_lines = [
                f"✅ 取得ルールを登録しました！",
                f"• *{name}*",
                f"• 種別: {category_labels.get(category_value, category_value)}",
            ]
            if category_value == "email":
                detail_lines.append(f"• 送信元: {rule.sender_email}")
                detail_lines.append(f"• 件名: {rule.subject_pattern}")
                fetch_label = "添付PDF" if rule.fetch_type == "attachment" else "リンク"
                detail_lines.append(f"• タイプ: {fetch_label}")
            elif category_value == "manual":
                if rule.url:
                    detail_lines.append(f"• URL: {rule.url}")
                if rule.notes:
                    detail_lines.append(f"• 備考: {rule.notes}")
            elif category_value == "scan":
                if rule.notes:
                    detail_lines.append(f"• 備考: {rule.notes}")

            client.chat_postMessage(
                channel=user_id,
                text="\n".join(detail_lines)
            )

        except Exception as e:
            logger.error(f"Error adding email rule: {e}")
            user_id = body["user"]["id"]
            client.chat_postMessage(
                channel=user_id,
                text=f"❌ 取得ルールの登録に失敗しました: {str(e)}"
            )

    @app.action("save_invoice_credit")
    def handle_save_invoice_credit(ack, body, client, action, say, logger):
        """クレジット請求書として保存"""
        ack()
        _open_save_invoice_modal(body, client, action, say, logger, "credit")

    @app.action("save_invoice_bank")
    def handle_save_invoice_bank(ack, body, client, action, say, logger):
        """銀行振込請求書として保存"""
        ack()
        _open_save_invoice_modal(body, client, action, say, logger, "bank")

    @app.action("save_invoice_sales")
    def handle_save_invoice_sales(ack, body, client, action, say, logger):
        """売上請求書として保存"""
        ack()
        _open_save_invoice_modal(body, client, action, say, logger, "sales")

    @app.action("save_invoice_pdf")
    def handle_save_invoice_pdf(ack, body, client, action, say, logger):
        """PDFを請求書として保存（後方互換）"""
        ack()
        _open_save_invoice_modal(body, client, action, say, logger, "credit")

    def _open_save_invoice_modal(body, client, action, say, logger, invoice_type: str):
        """請求書保存モーダルを開く共通ロジック"""
        file_id = action["value"]
        channel_id = body["channel"]["id"]

        type_names = {"credit": "クレジット", "bank": "銀行振込", "sales": "売上"}
        type_label = type_names.get(invoice_type, "クレジット")

        try:
            # ファイル情報を取得
            file_info = client.files_info(file=file_id)
            file_data = file_info["file"]
            file_name = file_data.get("name", "invoice.pdf")

            # PDFをダウンロードしてGemini解析で請求日・金額を抽出
            extracted_date = datetime.now().strftime("%Y-%m-%d")
            extracted_amount = ""
            download_url = file_data.get("url_private_download")
            if download_url:
                headers = {"Authorization": f"Bearer {os.getenv('SLACK_BOT_TOKEN')}"}
                dl_response = requests.get(download_url, headers=headers)
                if dl_response.status_code == 200:
                    try:
                        from api.services.invoice_fetcher import extract_invoice_data_with_gemini
                        pdf_info = extract_invoice_data_with_gemini(dl_response.content)
                        if pdf_info.get("date"):
                            extracted_date = pdf_info["date"]
                        if pdf_info.get("amount"):
                            extracted_amount = str(pdf_info["amount"])
                    except Exception as extract_err:
                        logger.warning(f"PDF extraction failed, using defaults: {extract_err}")

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

            # 金額の入力ブロック（抽出値があればプレースホルダーに反映）
            amount_element = {
                "type": "plain_text_input",
                "action_id": "amount_input",
                "placeholder": {"type": "plain_text", "text": "例: 10000"}
            }
            if extracted_amount:
                amount_element["initial_value"] = extracted_amount

            # private_metadata に file_id と invoice_type を格納
            metadata = json.dumps({"file_id": file_id, "invoice_type": invoice_type})

            client.views_open(
                trigger_id=body["trigger_id"],
                view={
                    "type": "modal",
                    "callback_id": "save_invoice_modal",
                    "private_metadata": metadata,
                    "title": {"type": "plain_text", "text": f"{type_label}請求書を保存"},
                    "submit": {"type": "plain_text", "text": "保存"},
                    "close": {"type": "plain_text", "text": "キャンセル"},
                    "blocks": [
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f"📄 *{file_name}* を{type_label}請求書として保存します。"
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
                            "element": amount_element
                        },
                        {
                            "type": "input",
                            "block_id": "date_block",
                            "label": {"type": "plain_text", "text": "請求日"},
                            "element": {
                                "type": "datepicker",
                                "action_id": "date_picker",
                                "initial_date": extracted_date
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

        # private_metadata から file_id と invoice_type を取得
        raw_metadata = view["private_metadata"]
        try:
            metadata = json.loads(raw_metadata)
            file_id = metadata["file_id"]
            invoice_type = metadata.get("invoice_type", "credit")
        except (json.JSONDecodeError, KeyError):
            # 後方互換: 旧形式（file_id のみ）
            file_id = raw_metadata
            invoice_type = "credit"

        user_id = body["user"]["id"]
        type_names = {"credit": "クレジット", "bank": "銀行振込", "sales": "売上"}
        type_label = type_names.get(invoice_type, "クレジット")

        try:
            values = view["state"]["values"]

            subscription_id = values["subscription_block"]["subscription_select"]["selected_option"]["value"]
            amount_str = values["amount_block"]["amount_input"]["value"]
            invoice_date = values["date_block"]["date_picker"]["selected_date"]

            # 金額をパース
            amount = float(amount_str.replace(",", "").replace("¥", "").replace("円", ""))

            # サブスク情報を取得
            file_naming_rule = "rename"  # デフォルト
            if subscription_id == "new":
                subscription_name = "未分類"
            else:
                subscription = spreadsheet_service.get_subscription_by_id(subscription_id)
                subscription_name = subscription.name if subscription else "不明"
                if subscription:
                    file_naming_rule = subscription.file_naming.value

            # ファイルをダウンロード
            file_info = client.files_info(file=file_id)
            file_data = file_info["file"]
            file_name = file_data.get("name", "invoice.pdf")
            download_url = file_data.get("url_private_download")

            headers = {"Authorization": f"Bearer {os.getenv('SLACK_BOT_TOKEN')}"}
            response = requests.get(download_url, headers=headers)

            if response.status_code != 200:
                raise Exception("ファイルのダウンロードに失敗しました")

            # ファイル命名ルールに応じたファイル名を生成
            if file_naming_rule == "original":
                upload_file_name = file_name
            else:
                # Rename: {date}_{vendor}_{amount}.pdf
                import re
                safe_vendor = re.sub(r'[\\/:*?"<>|]', '', subscription_name).strip() or "unknown"
                safe_amount = str(int(amount)) if amount else "0"
                upload_file_name = f"{invoice_date}_{safe_vendor}_{safe_amount}.pdf"

            # Google Driveにアップロード
            period = f"{datetime.strptime(invoice_date, '%Y-%m-%d').year}年{datetime.strptime(invoice_date, '%Y-%m-%d').month}月"
            upload_result = drive_service.upload_invoice(
                file_content=response.content,
                file_name=upload_file_name,
                mime_type="application/pdf",
                period=period,
                invoice_type=invoice_type
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
                invoice_type=invoice_type,
            )

            spreadsheet_service.add_invoice(invoice)

            # 確認メッセージ
            client.chat_postMessage(
                channel=user_id,
                text=(
                    f"✅ {type_label}請求書を保存しました！\n"
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

    @app.action("share_with_accountant")
    def handle_share_with_accountant(ack, body, client, action, logger):
        """税理士さんに請求書ファイルを共有"""
        ack()

        channel_id = body["channel"]["id"]
        period = action["value"]

        # ボタンを無効化して処理中メッセージに更新
        client.chat_update(
            channel=channel_id,
            ts=body["message"]["ts"],
            text=f"📤 {period} のファイルを税理士さんの共有フォルダにコピーしています...",
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"📤 *{period}* のファイルを税理士さんの共有フォルダにコピーしています..."
                    }
                }
            ]
        )

        try:
            # 請求書とサブスク情報を取得
            invoices = spreadsheet_service.get_invoices(period=period)
            subscriptions = spreadsheet_service.get_subscriptions()

            # サブスクIDから支払い方法を引けるマップを作成
            sub_payment_map = {}
            for sub in subscriptions:
                sub_payment_map[sub.id] = sub.payment_method

            # 請求書をカード/銀行に振り分け（売上はスキップ）
            card_file_ids = []
            bank_file_ids = []

            for inv in invoices:
                if not inv.drive_file_id:
                    continue

                # 売上請求書はカード/銀行の共有対象外
                if inv.invoice_type == "sales":
                    continue

                payment_method = sub_payment_map.get(inv.subscription_id)
                if payment_method == PaymentMethod.BANK or inv.invoice_type == "bank":
                    bank_file_ids.append(inv.drive_file_id)
                else:
                    # デフォルトはカード（サブスクが見つからない場合もカード扱い）
                    card_file_ids.append(inv.drive_file_id)

            # 共有フォルダにコピー
            share_result = drive_service.share_with_accountant(
                period=period,
                card_file_ids=card_file_ids,
                bank_file_ids=bank_file_ids
            )

            # 結果メッセージを作成
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

            # メッセージを更新
            client.chat_update(
                channel=channel_id,
                ts=body["message"]["ts"],
                text="\n".join(result_lines),
                blocks=[
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": "\n".join(result_lines)
                        }
                    }
                ]
            )

        except Exception as e:
            logger.error(f"Error sharing with accountant: {e}")
            client.chat_update(
                channel=channel_id,
                ts=body["message"]["ts"],
                text=f"❌ 税理士共有でエラーが発生しました: {str(e)}",
                blocks=[
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"❌ 税理士共有でエラーが発生しました: {str(e)}"
                        }
                    }
                ]
            )
