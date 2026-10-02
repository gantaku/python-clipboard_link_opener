# Clipboard Link Opener

コピーしたローカルファイルのパス（と http(s) の URL）を、貼り付けずにそのまま開く Windows 常駐ツール。
Claude Code が平文で出すパスを、Windows Terminal で選択（= コピー）するだけで開けるようにする。

## 使い方

1. `build\clipboard_link_opener.exe` を起動する（`pythonw run.py` でも可）。タスクトレイに青いアイコンが出る
2. パスだけを選択してコピーすると開く

| コピーしたもの | 開き方 |
|---|---|
| http(s) の URL | 既定のブラウザ |
| フォルダ | Explorer |
| 行番号つき（`app.py:42` `app.py#L42`） | VS Code でその行 |
| Obsidian の vault 内の `.md` | Obsidian |
| 実行系（.exe .bat .ps1 .py など） | 開かずに Explorer で選択表示 |
| それ以外のファイル | 既定のアプリ |

受け付ける表記: `C:\...` / `C:/...` / `~/...` / `/c/...` / `/mnt/c/...` / `file:///C:/...`。
前後のバッククォート・引用符・括弧・句読点、端末の折り返し改行は取り除く。

開かないもの: 文章の一部・相対パス・存在しないパス・許可ルートの外・UNC（`\\server\...`）・同じ文字列の 2 秒以内の再コピー。

## トレイメニュー

一時停止 / 設定ファイルを開く / ログを開く / 終了

## 設定

`%APPDATA%\clipboard_link_opener\config.json`（初回起動で作られる。保存すると次のコピーから反映）

```json
{
  "open_urls": true,
  "allowed_roots": ["~/knowledge", "~/workspaces", "~/.claude", "~/Downloads"],
  "reveal_extensions": [".bat", ".exe", "..."]
}
```

- URL を開くのをやめる: `"open_urls": false`
- 開いてよい場所を足す: `allowed_roots` に追加

ログ: `%APPDATA%\clipboard_link_opener\app.log`（開いた・拒否した理由が残る）

## スタートアップに登録する

1. `Win + R` → `shell:startup` で開いたフォルダに
2. `build\clipboard_link_opener.exe` のショートカットを置く

## ビルド・テスト

```cmd
build.bat
python -m pytest -q
```

Windows 10/11、Python 3.10+（ビルド時のみ）。
