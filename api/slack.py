"""Vercel serverless function entry point for Slack."""

import os
import json
from flask import Flask, request, Response
from slack_bolt import App as SlackApp
from slack_bolt.adapter.flask import SlackRequestHandler
from slack_sdk import WebClient

# Flask app
app = Flask(__name__)

# Slack App
slack_app = SlackApp(
    token=os.environ.get("SLACK_BOT_TOKEN"),
    signing_secret=os.environ.get("SLACK_SIGNING_SECRET"),
    process_before_response=True,
)

slack_handler = SlackRequestHandler(slack_app)


# === Slack Commands ===

@slack_app.command("/accounting-help")
def handle_help(ack, respond):
    """ヘルプを表示"""
    ack()
    respond({
        "response_type": "ephemeral",
        "text": """*経理自動化Bot ヘルプ*

*コマンド一覧:*
• `/accounting-help` - このヘルプを表示
• `/accounting-status` - 今月の経理状況を確認
• `/accounting-fetch-invoices` - メールから請求書を自動取得
• `/accounting-add-email-rule` - メール取得ルールを追加
• `/accounting-email-rules` - ルール一覧・削除
• `/accounting-invoices` - 取得済み請求書一覧

*使い方:*
1. `/accounting-add-email-rule` でメール取得ルールを設定
2. `/accounting-fetch-invoices` でメールから請求書を自動取得
3. カード/銀行のCSV明細をアップロード
4. CSV ↔ 請求書を照会
5. 不足している請求書がリストアップされます

*請求書の保存:*
PDFファイルをアップロードすると、Google Driveに保存できます。"""
    })


@slack_app.command("/accounting-status")
def handle_status(ack, respond):
    """状況を表示"""
    ack()

    try:
        from api.services.invoice_fetcher import invoice_fetcher

        # Spreadsheetから請求書数を取得
        result = invoice_fetcher.sheets.spreadsheets().values().get(
            spreadsheetId=invoice_fetcher.spreadsheet_id,
            range="invoices!A2:H1000"
        ).execute()
        invoices = result.get("values", [])

        # ルール数を取得
        rules_result = invoice_fetcher.sheets.spreadsheets().values().get(
            spreadsheetId=invoice_fetcher.spreadsheet_id,
            range="email_rules!A2:E100"
        ).execute()
        rules = rules_result.get("values", [])

        respond({
            "response_type": "ephemeral",
            "text": f"""📊 *経理状況*

• メール取得ルール: {len(rules)}件
• 取得済み請求書: {len(invoices)}件

`/accounting-fetch-invoices` でメールから請求書を取得できます。"""
        })
    except Exception as e:
        respond({
            "response_type": "ephemeral",
            "text": f"📊 *経理状況*\n\nデータ取得中にエラー: {str(e)}"
        })


@slack_app.command("/accounting-add-email-rule")
def handle_add_email_rule(ack, client, body):
    """メール取得ルール追加モーダル"""
    ack()
    client.views_open(
        trigger_id=body["trigger_id"],
        view={
            "type": "modal",
            "callback_id": "add_email_rule_modal",
            "title": {"type": "plain_text", "text": "メール取得ルール追加"},
            "submit": {"type": "plain_text", "text": "追加"},
            "close": {"type": "plain_text", "text": "キャンセル"},
            "blocks": [
                {
                    "type": "input",
                    "block_id": "name_block",
                    "label": {"type": "plain_text", "text": "ルール名（サービス名）"},
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "name_input",
                        "placeholder": {"type": "plain_text", "text": "例: AWS, GitHub, Notion"}
                    }
                },
                {
                    "type": "input",
                    "block_id": "sender_block",
                    "label": {"type": "plain_text", "text": "送信者メールアドレス"},
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "sender_input",
                        "placeholder": {"type": "plain_text", "text": "例: billing@aws.amazon.com"}
                    }
                },
                {
                    "type": "input",
                    "block_id": "subject_block",
                    "label": {"type": "plain_text", "text": "件名キーワード"},
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "subject_input",
                        "placeholder": {"type": "plain_text", "text": "例: Invoice, 請求書, ご利用明細"}
                    }
                },
                {
                    "type": "input",
                    "block_id": "type_block",
                    "label": {"type": "plain_text", "text": "取得タイプ"},
                    "element": {
                        "type": "static_select",
                        "action_id": "type_select",
                        "options": [
                            {
                                "text": {"type": "plain_text", "text": "PDF添付ファイル"},
                                "value": "attachment"
                            },
                            {
                                "text": {"type": "plain_text", "text": "メール内リンク"},
                                "value": "link"
                            }
                        ],
                        "initial_option": {
                            "text": {"type": "plain_text", "text": "PDF添付ファイル"},
                            "value": "attachment"
                        }
                    }
                }
            ]
        }
    )


@slack_app.view("add_email_rule_modal")
def handle_add_email_rule_submission(ack, body, client, view):
    """メール取得ルール追加の処理"""
    ack()

    try:
        values = view["state"]["values"]
        name = values["name_block"]["name_input"]["value"]
        sender = values["sender_block"]["sender_input"]["value"]
        subject = values["subject_block"]["subject_input"]["value"]
        fetch_type = values["type_block"]["type_select"]["selected_option"]["value"]

        from api.services.invoice_fetcher import invoice_fetcher

        # Spreadsheetに追加
        row = [name, sender, subject, fetch_type, ""]
        invoice_fetcher.sheets.spreadsheets().values().append(
            spreadsheetId=invoice_fetcher.spreadsheet_id,
            range="email_rules!A:E",
            valueInputOption="USER_ENTERED",
            body={"values": [row]}
        ).execute()

        user_id = body["user"]["id"]
        client.chat_postMessage(
            channel=user_id,
            text=f"""✅ メール取得ルールを追加しました！

• *ルール名*: {name}
• *送信者*: {sender}
• *件名キーワード*: {subject}
• *取得タイプ*: {"PDF添付" if fetch_type == "attachment" else "リンク"}

`/accounting-fetch-invoices` で請求書を取得できます。"""
        )

    except Exception as e:
        user_id = body["user"]["id"]
        client.chat_postMessage(
            channel=user_id,
            text=f"❌ ルール追加エラー: {str(e)}"
        )


@slack_app.command("/accounting-fetch-invoices")
def handle_fetch_invoices(ack, respond, client):
    """メールから請求書を自動取得"""
    ack()

    respond({
        "response_type": "ephemeral",
        "text": "📥 メールから請求書を取得中..."
    })

    try:
        from api.services.invoice_fetcher import invoice_fetcher

        results = invoice_fetcher.fetch_invoices(days_back=30)

        if results["errors"]:
            error_text = "\n".join(results["errors"][:3])
            respond({
                "response_type": "ephemeral",
                "text": f"""⚠️ *請求書取得完了（エラーあり）*

• 処理したメール: {results['processed']}件
• 保存した請求書: {results['saved']}件

*エラー:*
{error_text}"""
            })
        else:
            invoice_list = ""
            for inv in results["invoices"][:5]:
                invoice_list += f"\n• {inv.get('vendor', '不明')} ({inv.get('date', '')})"

            respond({
                "response_type": "ephemeral",
                "text": f"""✅ *請求書取得完了*

• 処理したメール: {results['processed']}件
• 保存した請求書: {results['saved']}件
{invoice_list if invoice_list else ''}

Google Driveに保存されました。"""
            })

    except Exception as e:
        respond({
            "response_type": "ephemeral",
            "text": f"❌ エラー: {str(e)}\n\nGmail APIの設定を確認してください。"
        })


@slack_app.command("/accounting-invoices")
def handle_invoices(ack, respond):
    """請求書一覧"""
    ack()

    try:
        from api.services.invoice_fetcher import invoice_fetcher

        result = invoice_fetcher.sheets.spreadsheets().values().get(
            spreadsheetId=invoice_fetcher.spreadsheet_id,
            range="invoices!A2:H100"
        ).execute()

        rows = result.get("values", [])

        if not rows:
            respond({
                "response_type": "ephemeral",
                "text": "📄 *請求書一覧*\n\nまだ請求書がありません。`/accounting-fetch-invoices` で取得してください。"
            })
            return

        invoice_list = ""
        for row in rows[-10:]:  # 最新10件
            vendor = row[1] if len(row) > 1 else "不明"
            amount = row[2] if len(row) > 2 else "-"
            date = row[3] if len(row) > 3 else ""
            url = row[5] if len(row) > 5 else ""

            if url:
                invoice_list += f"\n• <{url}|{vendor}> - {date} (¥{amount})"
            else:
                invoice_list += f"\n• {vendor} - {date} (¥{amount})"

        respond({
            "response_type": "ephemeral",
            "text": f"📄 *請求書一覧（最新10件）*\n{invoice_list}"
        })

    except Exception as e:
        respond({
            "response_type": "ephemeral",
            "text": f"❌ エラー: {str(e)}"
        })


@slack_app.command("/accounting-email-rules")
def handle_email_rules(ack, respond):
    """メール取得ルール一覧"""
    ack()

    try:
        from api.services.invoice_fetcher import invoice_fetcher

        result = invoice_fetcher.sheets.spreadsheets().values().get(
            spreadsheetId=invoice_fetcher.spreadsheet_id,
            range="email_rules!A2:E100"
        ).execute()

        rows = result.get("values", [])

        if not rows:
            respond({
                "response_type": "ephemeral",
                "text": "📧 *メール取得ルール一覧*\n\nルールがありません。`/accounting-add-email-rule` で追加してください。"
            })
            return

        blocks = [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "📧 *メール取得ルール一覧*"}
            },
            {"type": "divider"}
        ]

        for i, row in enumerate(rows):
            name = row[0] if len(row) > 0 else "不明"
            sender = row[1] if len(row) > 1 else "-"
            subject = row[2] if len(row) > 2 else "-"
            fetch_type = row[3] if len(row) > 3 else "attachment"
            type_text = "PDF添付" if fetch_type == "attachment" else "リンク"

            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*{name}*\n送信者: `{sender}`\n件名: `{subject}`\nタイプ: {type_text}"
                },
                "accessory": {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "削除"},
                    "style": "danger",
                    "action_id": "delete_email_rule",
                    "value": str(i + 2)  # 行番号（ヘッダー分+1、0始まり分+1）
                }
            })

        respond({
            "response_type": "ephemeral",
            "blocks": blocks
        })

    except Exception as e:
        respond({
            "response_type": "ephemeral",
            "text": f"❌ エラー: {str(e)}"
        })


@slack_app.action("delete_email_rule")
def handle_delete_email_rule(ack, body, client):
    """メール取得ルールを削除"""
    ack()

    row_num = body["actions"][0]["value"]
    user_id = body["user"]["id"]

    try:
        from api.services.invoice_fetcher import invoice_fetcher

        # 該当行をクリア
        invoice_fetcher.sheets.spreadsheets().values().clear(
            spreadsheetId=invoice_fetcher.spreadsheet_id,
            range=f"email_rules!A{row_num}:E{row_num}"
        ).execute()

        client.chat_postMessage(
            channel=user_id,
            text="✅ ルールを削除しました。`/accounting-email-rules` で確認してください。"
        )

    except Exception as e:
        client.chat_postMessage(
            channel=user_id,
            text=f"❌ 削除エラー: {str(e)}"
        )


@slack_app.command("/accounting-subscriptions")
def handle_subscriptions(ack, respond):
    """サブスク一覧（使わないが互換性のため残す）"""
    ack()
    respond({
        "response_type": "ephemeral",
        "text": "📋 この機能は `/accounting-email-rules` に置き換えられました。"
    })


@slack_app.command("/accounting-add-subscription")
def handle_add_subscription(ack, respond):
    """サブスク登録（使わないが互換性のため残す）"""
    ack()
    respond({
        "response_type": "ephemeral",
        "text": "📋 この機能は `/accounting-add-email-rule` に置き換えられました。"
    })


# === Slack Events ===

@slack_app.event("file_shared")
def handle_file_shared(event, client, say):
    """ファイルアップロード時の処理"""
    file_id = event.get("file_id")
    channel_id = event.get("channel_id")

    try:
        file_info = client.files_info(file=file_id)
        file_data = file_info["file"]
        file_name = file_data.get("name", "")
        file_type = file_data.get("filetype", "")

        if file_type == "csv" or file_name.endswith(".csv"):
            say(
                channel=channel_id,
                text=f"📄 CSV ファイル `{file_name}` を検出しました。\n照会処理を開始します..."
            )
            # TODO: CSV照会処理を実装
            say(
                channel=channel_id,
                text="✅ 照会機能は現在開発中です。"
            )
        elif file_type == "pdf" or file_name.endswith(".pdf"):
            say(
                channel=channel_id,
                text=f"📎 PDF ファイル `{file_name}` を検出しました。\n請求書として保存しますか？",
                blocks=[
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": f"📎 PDF ファイル `{file_name}` を検出しました。"}
                    },
                    {
                        "type": "actions",
                        "elements": [
                            {
                                "type": "button",
                                "text": {"type": "plain_text", "text": "請求書として保存"},
                                "style": "primary",
                                "action_id": "save_invoice_pdf",
                                "value": file_id
                            },
                            {
                                "type": "button",
                                "text": {"type": "plain_text", "text": "スキップ"},
                                "action_id": "skip_file",
                                "value": file_id
                            }
                        ]
                    }
                ]
            )
    except Exception as e:
        print(f"Error handling file: {e}")


@slack_app.action("save_invoice_pdf")
def handle_save_invoice_pdf(ack, body, client):
    """PDFを請求書として保存"""
    ack()

    file_id = body["actions"][0]["value"]
    user_id = body["user"]["id"]
    channel_id = body["channel"]["id"]

    try:
        from api.services.invoice_fetcher import invoice_fetcher
        import requests
        from datetime import datetime

        # ファイル情報を取得
        file_info = client.files_info(file=file_id)
        file_data = file_info["file"]
        file_name = file_data.get("name", "invoice.pdf")
        download_url = file_data.get("url_private_download")

        # ファイルをダウンロード
        headers = {"Authorization": f"Bearer {os.environ.get('SLACK_BOT_TOKEN')}"}
        response = requests.get(download_url, headers=headers)

        if response.status_code != 200:
            raise Exception("ファイルのダウンロードに失敗しました")

        # 期間を計算
        now = datetime.now()
        period = f"{now.year}年{now.month}月"

        # Google Driveに保存
        drive_result = invoice_fetcher.save_to_drive(
            response.content,
            file_name,
            period
        )

        # Spreadsheetに記録
        invoice_data = {
            "id": f"manual_{now.timestamp()}",
            "vendor": "手動アップロード",
            "amount": "",
            "date": now.strftime("%Y-%m-%d"),
            "source": "slack_upload",
            "drive_url": drive_result["web_view_link"],
            "status": "pending"
        }
        invoice_fetcher.record_invoice(invoice_data)

        client.chat_postMessage(
            channel=channel_id,
            text=f"✅ 請求書を保存しました！\n📁 <{drive_result['web_view_link']}|Google Driveで表示>"
        )

    except Exception as e:
        client.chat_postMessage(
            channel=channel_id,
            text=f"❌ 保存エラー: {str(e)}"
        )


@slack_app.action("skip_file")
def handle_skip_file(ack, body, client):
    """ファイルをスキップ"""
    ack()
    # メッセージを更新
    client.chat_update(
        channel=body["channel"]["id"],
        ts=body["message"]["ts"],
        text="ファイルをスキップしました。",
        blocks=[]
    )


# === Flask Routes ===

@app.route("/", methods=["GET"])
@app.route("/api/slack", methods=["GET"])
def health():
    """Health check"""
    return "Accounting Bot is running!"


@app.route("/", methods=["POST"])
@app.route("/api/slack", methods=["POST"])
def slack_events():
    """Handle Slack events"""
    return slack_handler.handle(request)
