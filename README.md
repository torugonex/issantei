# 一傘亭

ネットに流れる出来事を「世の中の空気の変化」として受け取り、表情を変えながら呟く架空の人物「一傘亭」のアプリです。

- 15分ごとに起き出して、呟くかどうかを決めます（起きている間は1時間に3〜4回）。
- 23時から6時までは眠っています。夜中に一度だけ寝言を言います。
- 朝いちばんの呟きは、その日の天気の一字（晴。雨。など）で始まります。
- 金曜の夜は、旧友とバーにいることがあります。
- 呟きと表情は、公開ページ（GitHub Pages）に表示されます。

## しくみ

```
GitHub Actions（15分ごと）
  └ python -m issantei.main
       1. 情報を集める ……… RSS（複数社）、気象庁の天気予報・地震情報、二十四節気
       2. 話題を束ねる ……… 似た見出しをまとめ、何社が報じたかで大小をはかる
       3. 話題を選ぶ ………… 新しい地震 → 大きな話題 → 小さな話題 → 暮らし、の順に重みをつけて
       4. 呟く ……………… Claude APIに人格と場面を渡し、呟き・表情・気分を受け取る
       5. 保存する ………… docs/data/tweets.json（公開）と state/state.json（気分など）
  └ 変更をコミット → GitHub Pages が docs/ を公開
```

## ファイル

| ファイル | 中身 |
| --- | --- |
| `issantei/persona.py` | 一傘亭の人格・口調・作法（呟きの指示文）。言葉づかいを直すときはここ |
| `issantei/main.py` | 一刻みの流れ。就寝時刻、呟く確率、皮肉の間隔などの数値は冒頭にまとめてある |
| `issantei/collect.py` | RSS、気象庁の天気・地震の取得 |
| `issantei/topics.py` | 見出しの束ね方と、深刻な出来事を見分ける語の一覧 |
| `issantei/season.py` | 二十四節気の計算、月ごとの草花 |
| `issantei/generate.py` | Claude APIの呼び出しと、作法に反した呟きの言い直し |
| `config/feeds.json` | 読むRSSの一覧と、天気の地域（初期値は茨城県南部・筑西） |
| `docs/index.html` | 公開ページ |
| `.github/workflows/tick.yml` | 15分ごとの定時実行 |
| `tests/simulate_day.py` | 模擬データで一日分を走らせる試運転 |

Pythonの標準ライブラリだけで動きます（追加のインストールは不要）。

## 導入の手順

1. **リポジトリを作る**　GitHubで新しいリポジトリ（例：`issantei`）を作り、このフォルダの中身をすべて入れます。
2. **APIキーを登録する**　リポジトリの Settings › Secrets and variables › Actions › New repository secret で、名前 `ANTHROPIC_API_KEY`、値にAnthropicのAPIキーを登録します。
3. **ページを公開する**　Settings › Pages で、Source を「Deploy from a branch」、Branch を `main`、フォルダを `/docs` にして保存します。
4. **試しに動かす**　Actions タブで「一傘亭」を選び、「Run workflow」を押します。起きている時間なら、確率次第で呟きます（呟かないこともあります）。
5. **ページを見る**　`https://（GitHubのユーザー名）.github.io/issantei/` を開きます。

あとは15分ごとに自動で動きます。GitHubの混み具合で、実行が数分から十数分遅れることがあります。

## 外部の時計から起こす（推奨）

GitHubの定時実行は取りこぼしが多く、15分ごとの指定でも半日に一度しか動かないことがあります。そこで、無料の [cron-job.org](https://cron-job.org/) から15分ごとに一傘亭を起こします。GitHubの定時実行は予備として残してあります。

1. **GitHubの合鍵（トークン）を作る**　GitHubの Settings › Developer settings › Personal access tokens › Fine-grained tokens › Generate new token。
   - Repository access：Only select repositories → `issantei`
   - Permissions：Actions を **Read and write**（ほかは不要）
   - 有効期限：任意（切れたら作り直して登録し直す）
   - 表示された文字列をコピーする（二度と表示されない）
2. **cron-job.org に登録する**　アカウントを作り、Create cronjob で次のように設定する。
   - URL：`https://api.github.com/repos/torugonex/issantei/actions/workflows/tick.yml/dispatches`
   - 実行間隔：15分ごと（Every 15 minutes）
   - Advanced › Request method：**POST**
   - Advanced › Headers：
     - `Authorization`：`Bearer （1の文字列）`
     - `Accept`：`application/vnd.github+json`
     - `User-Agent`：`issantei`
   - Advanced › Request body：`{"ref":"main"}`
3. **確かめる**　cron-job.org の実行履歴で、応答が **204** なら成功です（GitHubの Actions タブに実行が並びます）。401 はトークンの誤り、404 はURLか権限の誤りです。

確認のため必ず呟かせたいときは、Actions タブの Run workflow で「force」にチェックを入れて実行します。

## 調整のしかた

- **口調・作法を変える**：`issantei/persona.py` の文章を書き換えます。
- **呟く頻度**：`issantei/main.py` の `POST_PROB`（初期値0.85。15分ごとに呟く確率）。
- **皮肉の頻度**：同じく `SARCASM_GAP`（初期値4。皮肉のあと4回は控える）。
- **就寝・起床の時刻**：同じく `WAKE` と `SLEEP`。
- **使うAIのモデル**：Settings › Secrets and variables › Actions › Variables に `ISSANTEI_MODEL` を作ると変えられます。初期値は `claude-sonnet-5-5`。費用を抑えたいときは `claude-haiku-5-5`。
- **読むRSS**：`config/feeds.json` の `enabled` を切り替えます。

## 気をつけること

- **RSSの利用条件**：各社のRSSには利用条件があります。公開ページに出すことを前提に、使う前に各社の条件を確かめてください。初期設定ではGoogleニュースとNHKを有効にし、Yahoo!ニュースは無効にしてあります。
- **出典**：呟きのもとになった記事は、ページの帳面に「出典」としてリンクされます。呟きそのものは見出しの文言を引かず、一傘亭の言葉に言い換えています。
- **費用**：AnthropicのAPIは使った分だけ課金されます。一日60回前後の呼び出しで、一回あたりの入出力はごく短いものです。使用量はAnthropicのコンソールで確認できます。

## 試運転（APIを使わずに）

```
python tests/simulate_day.py 2026-10-08
```

`tests/fixtures/` の模擬データで、6時の起床から翌朝までの一日分を走らせ、いつ何を呟くかを表示します。呟きの文面はAPIを使わない仮のものです。
