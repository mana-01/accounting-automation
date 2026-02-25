"""Slack event handlers."""

import os
import requests
from slack_bolt import App

from ..services.csv_parser import csv_parser
from ..services.reconciliation import reconciliation_service
from ..services.spreadsheet import spreadsheet_service


def register_events(app: App):
    """イベントハンドラーを登録"""

    @app.event("file_shared")
    def handle_file_shared(event, client, say, logger):
        """ファイルがアップロードされたときの処理"""
        file_id = event.get("file_id")
        channel_id = event.get("channel_id")

        try:
            # ファイル情報を取得
            file_info = client.files_info(file=file_id)
            file_data = file_info["file"]

            file_name = file_data.get("name", "")
            file_type = file_data.get("filetype", "")
            mime_type = file_data.get("mimetype", "")

            # CSVファイルかチェック
            if file_type == "csv" or file_name.endswith(".csv"):
                say(
                    channel=channel_id,
                    text=f"📄 CSVファイル `{file_name}` を検出しました。照会処理を開始します..."
                )

                # ファイルをダウンロード
                download_url = file_data.get("url_private_download")
                if not download_url:
                    say(channel=channel_id, text="❌ ファイルのダウンロードURLが取得できませんでした。")
                    return

                headers = {"Authorization": f"Bearer {os.getenv('SLACK_BOT_TOKEN')}"}
                response = requests.get(download_url, headers=headers)

                if response.status_code != 200:
                    say(channel=channel_id, text="❌ ファイルのダウンロードに失敗しました。")
                    return

                csv_content = response.content

                # CSVをパース
                try:
                    transactions = csv_parser.parse(csv_content)
                    say(
                        channel=channel_id,
                        text=f"✅ {len(transactions)}件の取引を読み込みました。照会を実行します..."
                    )
                except Exception as e:
                    say(channel=channel_id, text=f"❌ CSVのパースに失敗しました: {str(e)}")
                    return

                # 照会を実行
                try:
                    result = reconciliation_service.reconcile(transactions)

                    # 結果を報告
                    blocks = [
                        {
                            "type": "header",
                            "text": {"type": "plain_text", "text": "🔍 照会結果"}
                        },
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": (
                                    f"*期間:* {result.period}\n"
                                    f"*取引数:* {result.total_transactions}件\n"
                                    f"*マッチ:* ✅ {result.matched_count}件\n"
                                    f"*不足:* ❌ {result.unmatched_count}件"
                                )
                            }
                        }
                    ]

                    # 不足請求書があればリストアップ
                    if result.missing_invoices:
                        missing_report = reconciliation_service.generate_missing_invoices_report(
                            result.missing_invoices
                        )
                        blocks.append({
                            "type": "section",
                            "text": {"type": "mrkdwn", "text": missing_report}
                        })
                    else:
                        # 全件一致の場合、税理士共有ボタンを表示
                        blocks.append({
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": "✅ すべての取引に対応する請求書が揃っています！"
                            }
                        })
                        blocks.append({
                            "type": "actions",
                            "elements": [
                                {
                                    "type": "button",
                                    "text": {"type": "plain_text", "text": "税理士さんに共有する"},
                                    "style": "primary",
                                    "action_id": "share_with_accountant",
                                    "value": result.period
                                }
                            ]
                        })

                    say(channel=channel_id, blocks=blocks)

                except Exception as e:
                    logger.error(f"Reconciliation error: {e}")
                    say(channel=channel_id, text=f"❌ 照会処理でエラーが発生しました: {str(e)}")

            # PDFファイルの場合（請求書として保存）
            elif file_type == "pdf" or mime_type == "application/pdf":
                say(
                    channel=channel_id,
                    text=(
                        f"📄 PDFファイル `{file_name}` を検出しました。\n"
                        "保存先を選択してください。"
                    ),
                    blocks=[
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f"📄 PDFファイル `{file_name}` を検出しました。\n保存先を選択してください。"
                            }
                        },
                        {
                            "type": "actions",
                            "elements": [
                                {
                                    "type": "button",
                                    "text": {"type": "plain_text", "text": "クレジット請求書"},
                                    "style": "primary",
                                    "action_id": "save_invoice_credit",
                                    "value": file_id
                                },
                                {
                                    "type": "button",
                                    "text": {"type": "plain_text", "text": "銀行振込請求書"},
                                    "action_id": "save_invoice_bank",
                                    "value": file_id
                                },
                                {
                                    "type": "button",
                                    "text": {"type": "plain_text", "text": "売上請求書"},
                                    "action_id": "save_invoice_sales",
                                    "value": file_id
                                },
                                {
                                    "type": "button",
                                    "text": {"type": "plain_text", "text": "スキップ"},
                                    "action_id": "skip_file"
                                }
                            ]
                        }
                    ]
                )

        except Exception as e:
            logger.error(f"File handling error: {e}")

    @app.event("app_mention")
    def handle_app_mention(event, say):
        """ボットがメンションされたときの処理"""
        user = event.get("user")
        text = event.get("text", "").lower()

        if "ヘルプ" in text or "help" in text:
            say(
                f"<@{user}> こんにちは！\n"
                "使い方は `/accounting-help` で確認できます。\n"
                "CSVファイルをアップロードすると、自動で照会処理を行います。"
            )
        elif "状況" in text or "status" in text:
            period = spreadsheet_service.get_current_period()
            say(f"<@{user}> 現在の期間は *{period}* です。\n`/accounting-status` で詳細を確認できます。")
        else:
            say(
                f"<@{user}> お呼びですか？\n"
                "• CSVアップロード → 自動照会\n"
                "• `/accounting-help` → ヘルプ表示"
            )

    @app.event("message")
    def handle_message(event, logger):
        """メッセージイベント（ログ用）"""
        # file_sharedで処理するので、ここでは特に何もしない
        pass
