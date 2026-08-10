# GCP Project Onboarder

Microsoft Teams のチャネルメッセージから、Google Workspace グループのメンバー追加・削除を自動化するツールです。

あらかじめ Google Cloud 側で Google グループに各種 IAM ロール（アクセス権限）を割り当てておくことで、本ツールを通じてメンバーのオンボーディング・オフボーディング（グループへの追加・削除）を行うだけで、対象ユーザーへの権限付与・剥奪が安全かつ自動的に完結します。

---

## できること

1. **Googleグループ メンバー追加**
   - 指定した Google Workspace グループにユーザーを追加します。
   - `DEFAULT_GROUP_EMAIL` を設定している場合、メッセージ内でグループアドレスを省略できます。
2. **Googleグループ メンバー削除**
   - 指定した Google Workspace グループからユーザーを削除します。
3. **入力情報の不足検知と案内**
   - メッセージ内にユーザーのメールアドレスが含まれていない場合、メールアドレスの明記を促す案内メッセージを返信します。

---

## 仕組み・インプットとアウトプット

### インプット (Input)
Teams チャネルで `@GCP Onboarder` 宛てに自然言語メッセージを投稿します。

```text
# グループとユーザーを指定する場合
@GCP Onboarder 開発チーム (group-dev@example.com) に 山田さん (yamada@example.com) を追加して

# デフォルトグループを使用する場合（DEFAULT_GROUP_EMAIL 設定時）
@GCP Onboarder 山田さん (yamada@example.com) を追加して

# メールアドレスを省略した場合（案内メッセージが返信されます）
@GCP Onboarder 山田さんを追加して
```

### アウトプット (Output)
1. **依頼チャネルへの受付応答**
   - メッセージ受信後、即座（0.05秒以下）に受付完了（または不足情報の案内）を返信します。
2. **管理者チャネルへの承認リクエスト送信**
   - 管理者専用チャネルに承認ボタン付きの Adaptive Card を自動送信します。
3. **処理完了通知**
   - 管理者が承認ボタンを押すと API が実行され、依頼チャネルおよび管理者チャネルに完了報告が送信されます。

---

## 🔑 Google アカウント（サービスアカウント）の設定方法

本ツールが Google API を呼び出す際に使用するサービスアカウントの設定方法です。

### 1. 本番環境 (Cloud Run) での設定
Cloud Run にデプロイする際、実行用のサービスアカウントを指定します。

```bash
# 例: サービスアカウントおよび Secret Manager を指定して Cloud Run デプロイ
gcloud run deploy gcp-project-onboarder \
    --image gcr.io/<PROJECT_ID>/gcp-project-onboarder \
    --region asia-northeast1 \
    --service-account="gcp-bot-sa@<PROJECT_ID>.iam.gserviceaccount.com" \
    --set-secrets="TEAMS_SECURITY_TOKEN=TEAMS_SECURITY_TOKEN:latest"
```

### 2. Google Workspace グループ側での権限設定
管理対象の Google Workspace グループ（例: `group-dev@company.com`）のメンバー管理画面にて、サービスアカウントのアドレス (`gcp-bot-sa@<PROJECT_ID>.iam.gserviceaccount.com`) を **マネージャー (Manager)** ロールとして追加します。

> [!IMPORTANT]
> **組織の Google Workspace 設定に関する注意点**
> サービスアカウントのアドレス (`@<PROJECT_ID>.iam.gserviceaccount.com`) は Google Workspace 側から見ると「組織外（外部）のメールアドレス」として扱われます。
> グループにサービスアカウントを追加する際は、グループ設定（または Google Admin コンソール）で **「組織外のメンバーを許可 (Allow external members)」** を **ON** に設定してください。

### 3. ローカル開発環境での認証設定
ローカル環境で動作テストを行う場合は、以下のいずれかの方法で認証を通します：

```bash
# 方法A: gcloud CLI によるアプリケーションデフォルト認証 (推奨)
gcloud auth application-default login

# 方法B: サービスアカウント JSON 鍵ファイルの指定
export GOOGLE_APPLICATION_CREDENTIALS="/path/to/service-account-key.json"
```

---

## システム構成とセキュリティの特徴

### データベース不要 (DB-less) 構成
本システムは、データベースの保持や構築が不要な**「データベースレス (DB-less)」**アーキテクチャを採用しています。

### なぜ HMAC-SHA256 署名トークンが必要なのか？
データベースを持たない構成で「申請から承認・実行」までを安全に行うため、**HMAC-SHA256 署名トークン** を利用しています。

```text
[申請メッセージ] ──> [署名トークン生成 (RAM内の暗号鍵で署名)] ──> [承認ボタン(Adaptive Card)に保持]
                                                                          │
[自動実行完了]  <── [トークン検証 (改ざん・有効期限チェック)] <── [管理者が承認ボタンを押す]
```

1. **データ改ざんの防止（セキュリティ）**
   - 承認ボタンに付与されるトークンには「誰を」「どのグループに」追加・削除するかの情報が含まれます。
   - ボットが持つ暗号鍵で電子署名（HMAC-SHA256）を行うため、第三者がトークン内の対象ユーザーをすり替えたり改ざんしたりしても、署名検証で即座に拒否されます。
2. **有効期限の強制とステートレス化（運用コスト削減）**
   - トークン内部に有効期限（デフォルト3日間）が埋め込まれており、期限切れの承認操作は自動的に無効化されます。
   - 申請データをデータベースに保存・管理・削除する必要がなくなり、漏洩リスクや運用コストをゼロに抑えられます。

---

## 設定パラメータ一覧

| パラメータ名 | デフォルト値 / 設定例 | 説明 |
| :--- | :--- | :--- |
| **`GEMINI_MODEL_NAME`** | `gemini-flash-lite` | 自然言語解析に使用する Gemini のモデル名。 |
| **`DEFAULT_GROUP_EMAIL`** | 空 (例: `group-dev@example.com`) | メッセージ内でグループメールアドレスが省略された場合に使用されるデフォルトのグループアドレス。 |
| **`ALLOWED_EMAIL_DOMAINS`** | 空 (例: `example.com`) | 申請を許可するユーザーのメールアドレスドメインのカンマ区切りリスト。空の場合はドメイン制限なし。 |
| **`TOKEN_TTL_SECONDS`** | `259200` (3日間) | 承認ボタン（トークン）の有効期限（秒）。 |
| **`LLM_COST_ENABLE_CACHE`** | `true` | メッセージ解析結果のインメモリキャッシュを有効化。同一内容の申請における API 呼び出しを削減します。 |
| **`LLM_COST_CACHE_TTL_SECONDS`** | `2592000` (30日間) | メッセージ解析結果キャッシュの有効期限（秒）。 |
| **`LLM_COST_MAX_OUTPUT_TOKENS`** | `150` | Gemini からの応答出力トークン数の上限値。 |

---

## ディレクトリ構造

```
gcp-project-onboarder/
├── app/
│   ├── main.py                   # FastAPI Webhook, 背景タスク, 承認エンドポイント
│   ├── config.py                 # アプリケーション設定管理
│   ├── security/
│   │   ├── hmac_verifier.py      # Teams HMAC-SHA256 署名検証
│   │   ├── token_service.py      # DBレス HMAC 署名トークン生成・検証
│   │   └── guardrails.py         # 入力検証・セキュリティガードレール
│   └── services/
│       ├── llm_parser.py         # Gemini による自然言語解析
│       ├── workspace_service.py  # Google Cloud Identity Groups API 連携
│       ├── teams_notifier.py     # Teams 通知・Adaptive Card 送信
│       └── secret_manager_service.py # Secret Manager 連携
├── tests/                        # テストコード
├── Dockerfile                    # Docker イメージビルド用ファイル
├── requirements.txt              # 依存パッケージ
├── .env.example                  # 環境変数設定サンプル
└── README.md
```

---

## ローカルでの開発・テスト

```bash
# 仮想環境の作成とパッケージインストール
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# テストの実行
pytest -v
```

---

## Cloud Run へのデプロイ

```bash
# 1. Secret Manager へ Teams HMAC トークンを登録
gcloud secrets create TEAMS_SECURITY_TOKEN --data-file=- <<< "your_teams_outgoing_webhook_hmac_token_base64"

# 2. コンテナイメージのビルド
gcloud builds submit --tag gcr.io/<PROJECT_ID>/gcp-project-onboarder

# 3. Cloud Run へのデプロイ (Secret Manager 連携)
gcloud run deploy gcp-project-onboarder \
    --image gcr.io/<PROJECT_ID>/gcp-project-onboarder \
    --region asia-northeast1 \
    --service-account="gcp-bot-sa@<PROJECT_ID>.iam.gserviceaccount.com" \
    --allow-unauthenticated \
    --set-secrets="TEAMS_SECURITY_TOKEN=TEAMS_SECURITY_TOKEN:latest"
```

---

## 謝辞

このプロジェクトの開発は、Google Antigravityの支援を受けて行われました。
