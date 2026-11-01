# challenge-video-editor (vedit)

Google AI Challenge 応募用の 90 秒動画を、**カット表（台本データ）から ffmpeg で自動編集・書き出し**するパイプラインです。
動画編集ソフトでの手作業をやめて、「カット表を直す → コマンド 1 発で再レンダリング」で何度でも同じ結果を再現できるようにすることが目的です。

- 入力: カット表 (JSON または CSV)
- 出力: テロップ・効果・BGM・効果音入りの MP4 1 本 (16:9 1080p。`--aspect 9:16` で縦動画に切り替え)
- 依存: **ffmpeg (libass 付き) と Python 3.10 以降だけ**です。pip / npm パッケージは使っていません（標準ライブラリのみ）

![30秒サンプルのコンタクトシート (16:9)](docs/sample_30s_contact_sheet.jpg)
![同じカット表の縦動画版 (9:16)](docs/sample_30s_9x16_contact_sheet.jpg)

---

## 目次
1. [セットアップ](#1-セットアップ)
2. [クイックスタート（サンプル動画の作り方）](#2-クイックスタートサンプル動画の作り方)
3. [実行コマンド](#3-実行コマンド)
4. [カット表の書式 (JSON)](#4-カット表の書式-json)
5. [カット表の書式 (CSV)](#5-カット表の書式-csv)
6. [編集技術と実装方法](#6-編集技術と実装方法)
7. [テストレンダリング結果](#7-テストレンダリング結果)
8. [仕組み・ファイル構成](#8-仕組みファイル構成)
9. [素材と著作権について](#9-素材と著作権について)

---

## 1. セットアップ

| 必要なもの | 確認コマンド | 備考 |
|---|---|---|
| ffmpeg / ffprobe 4.4 以降 (6.x で動作確認) | `ffmpeg -filters \| grep -E " (ass\|zoompan\|sidechaincompress\|silencedetect) "` | **libass が有効**なビルドが必要 (`ass` フィルタ) |
| Python 3.10 以降 | `python3 --version` | 標準ライブラリのみ |
| 日本語フォント | `fc-list :lang=ja` | 既定は `IPAGothic`。太いフォントを推奨 |

```bash
# Ubuntu / Debian
sudo apt-get install -y ffmpeg fonts-ipafont-gothic
# (任意) より太いテロップ向けフォント
sudo apt-get install -y fonts-noto-cjk-extra
```

```bash
# macOS (Homebrew)
brew install ffmpeg
```

- ダミー素材生成で使う `flite`（ffmpeg 内蔵の英語 TTS）は無くても動きます（トーン音で代用）。
- フォントは `project.font` でフォント名を指定します。手元の `.ttf/.otf` を使う場合は `project.fonts_dir` にフォルダを指定してください（例: `"fonts_dir": "fonts"`）。

## 2. クイックスタート（サンプル動画の作り方）

```bash
python3 tools/make_dummy_assets.py
```

```bash
python3 -m vedit render examples/sample_30s.json
```

これで `out/sample_30s.mp4`（30 秒、1920x1080）ができます。`make` が使える環境なら `make sample` でも同じです。

1 行目でテスト用の素材を **ffmpeg だけで生成**します（外部ダウンロードはありません）。出力先は `examples/assets/` です。

| ファイル | 内容 | 用途 |
|---|---|---|
| `talk.mp4` | testsrc2 + 合成音声。文の間に 0.6〜1.6 秒の無音あり | 話者カメラ / ジャンプカット |
| `broll.mp4` | SMPTE カラーバー + 440Hz | B ロール |
| `wipe.mp4` | 小さいテストパターン | ワイプ (PiP) |
| `photo1.png` / `photo2.png` | 4K のマンデルブロ集合 / グラデーション+グリッド | Ken Burns / パン |
| `nar_*.wav` | flite による英語ナレーション | ナレーション |
| `bgm.wav` | sine 波で合成したコード進行 (C-Am-F-G) + キック、32 秒 | BGM (ループ再生) |
| `se_pop.wav` / `se_whoosh.wav` / `se_impact.wav` | sine 波・ノイズで合成 | 効果音 |

その他のサンプル:

```bash
python3 -m vedit render examples/sample_30s.json --aspect 9:16
```

```bash
python3 -m vedit render examples/sample_30s.csv --preview
```

```bash
python3 -m vedit render examples/template_90s.json
```

それぞれ、縦動画版 `out/sample_30s_9x16.mp4`、CSV 版の 540p プレビュー、本番用 90 秒構成テンプレート `out/challenge_90s.mp4` を出力します。

## 3. 実行コマンド

```
python3 -m vedit render <カット表.json|.csv> [オプション]

  -o, --output PATH   出力 MP4 (省略時は project.output。カット表のあるフォルダ基準)
  --aspect RATIO      16:9 / 9:16 / 1:1 / 4:5 にアスペクト比を上書き (出力名に _9x16 などが付く)
  --project PATH      CSV 用のプロジェクト設定 JSON (省略時は <csv名>.project.json を自動で読む)
  --preview           540p・高速設定で試し書き出し
  --dry-run           レンダリングせず、タイムライン計算と ASS 字幕の生成だけ行う (書式チェックに便利)
  --keep-temp         中間ファイル (カットごとのセグメント、telops.ass) を out/.work_<名前>/ に残す
  -v, --verbose       実行した ffmpeg コマンドをすべて表示
```

出力されるファイル:

| ファイル | 内容 |
|---|---|
| `out/<名前>.mp4` | 完成動画 (H.264 High / AAC 192k / +faststart / -14 LUFS) |
| `out/<名前>.render.log` | 実行ログ (カットごとの長さ、ジャンプカットで何秒削ったか、所要時間) |
| `out/<名前>.render.json` | 結果 (成功/失敗、長さ、解像度、タイムライン) |
| `out/<名前>.ass` | 焼き込んだテロップの ASS 字幕 (Aegisub などで微調整の確認用) |

失敗した場合は終了コード 1 で、`{"status": "failed", "error": ...}` を出力します。

単体テスト (ffmpeg 不要の部分):

```bash
python3 -m unittest discover -s tests
```

## 4. カット表の書式 (JSON)

全体の形は次のとおりです。完全な例は [`examples/sample_30s.json`](examples/sample_30s.json) と [`examples/template_90s.json`](examples/template_90s.json) を見てください。

```jsonc
{
  "project": { ... 動画全体の設定 ... },
  "cuts": [ { ... カット1 ... }, { ... カット2 ... } ]
}
```

ファイルパスはすべて **カット表のあるフォルダからの相対パス** で書きます。

### 4.1 project（全体設定）

| キー | 既定値 | 説明 |
|---|---|---|
| `title` | `"untitled"` | MP4 のメタデータに入るタイトル |
| `aspect` | `"16:9"` | `16:9` / `9:16` / `1:1` / `4:5` |
| `resolution` | `1080` | 短辺の px (16:9 なら 1920x1080、9:16 なら 1080x1920) |
| `fps` | `30` | フレームレート |
| `target_duration` | なし | 目標秒数 (例 `90`)。0.5 秒以上ずれると警告 |
| `font` / `fonts_dir` | `"IPAGothic"` / なし | テロップの既定フォント、フォントフォルダ |
| `output` | `"out/output.mp4"` | 出力先 |
| `video` | `{"crf": 20, "preset": "medium"}` | x264 の設定 |
| `audio` | `{"loudnorm": true, "target_lufs": -14}` | 2 パスのラウドネス正規化 (YouTube 推奨の -14 LUFS) |
| `bgm` | なし | BGM とダッキングの設定 (下記) |
| `styles` | 組み込み 6 種 | テロップのスタイル定義 (下記) |
| `cut_types` | `normal` / `hook` / `title` | カットのプリセット (下記) |
| `defaults` | 下記 | `fit`、`jumpcut` の既定値、`narration_tail` (ナレーション後の余白 0.35 秒) |
| `overrides` | なし | アスペクト比ごとの上書き (下記) |

**bgm**

```json
"bgm": {
  "file": "assets/bgm.wav",   // 短い素材は自動でループする
  "volume": 0.4, "fade_in": 0.5, "fade_out": 2.0,
  "ducking": { "enabled": true, "threshold": 0.04, "ratio": 6, "attack": 15, "release": 350, "makeup": 1 }
}
```

**overrides**（縦動画への切り替え）: `overrides["9:16"]` の中身は、`--aspect 9:16` のとき（または `aspect` が `9:16` のとき）に project へ deep-merge されます。`cuts` キーを書けばカット ID ごとの上書きもできます。

```json
"overrides": {
  "9:16": {
    "styles": { "default": { "size": 64, "margin_v": 330 } },
    "cuts":   { "talk": { "fit": "blur" },
                "broll": { "pip": { "source": "assets/wipe.mp4", "position": "tr", "scale": 0.5 } } }
  }
}
```

### 4.2 cuts（カット）

| キー | 型 | 説明 |
|---|---|---|
| `id` | 文字列 | カット名 (ログや overrides で使う) |
| `type` | `normal` / `hook` / `title` / 独自 | カットのプリセット (§4.4) |
| `source` | パス / `"color:#RRGGBB"` | 動画・画像・単色背景 |
| `in` / `out` | 秒 | 動画素材の使用範囲 (省略時は全体) |
| `duration` | 秒 / `"narration"` | カットの長さ。`"narration"` ならナレーションの長さに合わせる。省略時は 動画: `out-in` (ジャンプカット後)、画像・単色: ナレーション長、それも無ければ 3 秒 |
| `fit` | `cover` / `contain` / `blur` | 画面への収め方。`blur` はぼかした背景で余白を埋める (縦動画向け) |
| `source_volume` | 数値 | 素材動画の元音声の音量 (`0` でミュート)、既定は `1` |
| `telop` | 文字列 / オブジェクト / 配列 | テロップ (§4.3) |
| `effects` | 配列 | 画面効果 (§6.3) |
| `pip` | オブジェクト / 配列 | ワイプ (§6.3) |
| `narration` | パス / オブジェクト | `{"file": "...", "offset": 0.4, "volume": 1.0}` 。offset はカット頭からの開始秒 |
| `jumpcut` | `true` / オブジェクト | 無音区間の自動カット (§6.2) |
| `se` | 配列 | 効果音 `[{"file": "...", "at": 0.5, "volume": 1.0}]`。`at` はカット頭からの秒 |

### 4.3 テロップ

```jsonc
"telop": "そのまま文字列でも OK"
"telop": { "text": "無音を|**自動で**|カット", "style": "subtitle", "anim": "words",
           "start": 0.0, "end": 2.5, "word_interval": 0.2, "pos": [0.5, 0.8] }
"telop": [ {...}, {...} ]   // 複数 (時間差や上下に同時表示)
```

- `start` / `end`: カット頭からの秒。`end` に負の値を書くとカット末尾からの秒になります。省略時はカット全体。ジャンプカット後のタイムラインが基準です
- `pos`: 任意の位置 (画面に対する割合 `[x, y]`)

**テキスト記法（単語単位の色変え・サイズ強調）**

| 記法 | 意味 |
|---|---|
| `**強調**` | スタイルの `highlight` (色・倍率) を適用 |
| `{c=#FF0000,s=1.4,oc=#000000}語{/}` | 任意の文字色 `c`、倍率 `s`、縁取り色 `oc` を指定 |
| `\|` | 単語の区切り（表示されない）。`words` / `karaoke` アニメの単位。半角スペースも区切りになる |
| 改行 (`\n`) | 改行 |

**アニメーション `anim`**

| 値 | 動き |
|---|---|
| `pop` | 0% → 118% → 100% に拡大しながら出現 (YouTuber 定番のポップイン) |
| `words` | 単語ごとに順番に「ポンッ」と表示（縦方向に弾ませるので行がガタつかない） |
| `karaoke` | 全文表示のまま、単語ごとに順番にハイライト色へ変化 |
| `typewriter` | 1 文字ずつ表示 |
| `slide_up` | 下からスライドしながらフェードイン |
| `fade` / `none` | フェード / アニメなし |

**スタイル `styles`**（組み込み: `default`, `hook`, `emphasis`, `title`, `label` と、各自定義のもの。`default` 以外は `default` を継承）

```jsonc
"styles": {
  "subtitle": {
    "font": null,                 // null なら project.font
    "size": 76,                   // 短辺 1080px 基準の px (解像度に応じて自動スケール)
    "color": "#FFFFFF",           // 文字色
    "outline_color": "#000000", "outline": 7,          // 太い縁取り
    "outer_outline_color": "#1E64FF", "outer_outline": 6,  // 二重縁取り (外側)
    "shadow": 3, "shadow_color": "#000000",
    "bold": true,
    "align": "bottom",            // bottom / center / top / top_left / bottom_right ... または 1-9
    "margin_v": 70, "margin_h": 80,
    "box": false, "box_color": "#000000", "box_alpha": 0.35,  // 座布団 (帯) 付きテロップ
    "highlight": { "color": "#FFE600", "scale": 1.3 },       // ** ** の強調設定
    "anim": "pop"                 // 既定アニメ
  }
}
```

### 4.4 カットタイプ（構成支援）

`type` で指定するプリセットです。カット側に書いた値が優先されます。`project.cut_types` に独自のプリセットも追加できます。

| type | 中身 | 用途 |
|---|---|---|
| `hook` | 単色背景 + 画面中央の巨大テロップ (`hook` スタイル: 黄文字・黒縁・白の二重縁、強調は赤) + `pop` + 白フラッシュ + 揺れ + ゆっくりズームイン、3 秒 | **冒頭フック**。`{"type": "hook", "telop": "90秒で\n**未来**が変わる"}` だけで成立 |
| `title` | 単色背景 + 中央テロップ + フェードイン/アウト、2.5 秒 | 章タイトル・CTA |
| `normal` | なし | 通常カット |

## 5. カット表の書式 (CSV)

スプレッドシートで台本を管理したい場合は CSV が使えます。1 行が 1 カットです。例は [`examples/sample_30s.csv`](examples/sample_30s.csv) を見てください（JSON 版と同じ動画になります）。

- 全体設定は同名の `<csv名>.project.json` に書きます（`{"project": {...}}`。例: [`examples/sample_30s.project.json`](examples/sample_30s.project.json)）。`--project` で別のファイルも指定できます
- `id` が `#` で始まる行はコメント扱いになります
- カンマを含むセルはダブルクォートで囲みます（スプレッドシートから書き出せば自動）

| 列 | 例 |
|---|---|
| `id,type,source,in,out,duration,fit` | JSON と同じ |
| `telop,telop_style,telop_anim,telop_start,telop_end` | `無音を\|**自動で**\|カット`, `subtitle`, `words` 。セル内の `\n` は改行になる |
| `effects` | `punch_in:start=3.2,to=1.18; shake:start=3.2,duration=0.3`（`;` 区切り、`名前:key=val,...`） |
| `pip` | `source=assets/wipe.mp4,position=tr,scale=0.3,start=0.3` |
| `narration,narration_offset` | `assets/nar.wav`, `0.4` |
| `source_volume` | `0` |
| `jumpcut` | `true` または `noise=-35dB,min_silence=0.35` または `target=narration` |
| `se` | `assets/se_pop.wav@0.15; assets/se_whoosh.wav@2*0.8`（`ファイル@秒*音量`） |

`effects` / `pip` / `jumpcut` / `se` のセルには JSON をそのまま書くこともできます（`[{"type":"kenburns", ...}]`）。

## 6. 編集技術と実装方法

### 6.1 テロップ（ASS 字幕 + libass）
- カット表から 1 本の ASS ファイルを生成し、最終合成で `ass` フィルタで焼き込みます（`vedit/telop.py`）
- **太い縁取り**: ASS の Outline。**二重縁取り**（白文字・黒縁・外側に色縁）は、同じ文字を Layer 0（太い外縁）と Layer 1（内縁）に重ねて描画
- **単語単位の色・サイズ**: 単語ごとに `\1c` `\3c` `\fscx` `\fscy` タグを付けています。ASS のタグは後ろの文字にも残るので、単語ごとに既定値へ戻すタグも出力しています
- **ポップイン**: `\t()` による拡大アニメ。`words` は `\alpha` と `\fscy` を単語ごとに遅延させています
- 文字サイズは「短辺 1080px 基準」で、解像度やアスペクト比に応じて自動でスケールします
- 注意: libass は `Default` という名前のスタイルを特別扱いするので、内部では `T_` を付けたスタイル名で出力しています

### 6.2 ジャンプカット（silencedetect）
- `jumpcut: true` のカットは、まず `silencedetect`（既定: `-35dB` 以下が `0.35` 秒以上続く区間）で無音区間を検出します
- 喋っている区間の前後に `padding`（0.08 秒）の余白を付けて残し、`select` / `aselect` で映像と音声を同じ区間だけ切り出して詰めます（音声は `asetnsamples=480` で 10ms 単位に揃えています）
- `target`: `source`（既定。素材動画の音声で判定し、映像も一緒にカット）か `narration`（別録りナレーションの無音だけを詰める）
- テロップ・SE の時刻は **カット後** のタイムラインで解釈されます
- サンプルでは talk カットが **13.15 秒 → 6.78 秒**、broll のナレーションが **6.91 秒 → 4.38 秒** に詰められました

### 6.3 画面効果

| `effects[].type` | パラメータ | 実装 |
|---|---|---|
| `zoom_in` / `zoom_out` | `from`, `to`, `x`, `y` (中心 0-1), `start`, `duration`, `ease` (`inout`/`out`/`in`/`linear`) | `zoompan`（2 倍解像度に拡大してから処理し、ガタつきを抑える） |
| `kenburns` | `from: {zoom,x,y}`, `to: {zoom,x,y}` | 同上（ズームしながらパン） |
| `pan` | `direction` (`left`/`right`/`up`/`down`), `zoom` | 同上 |
| `punch_in` | `start`, `to` (1.3), `duration` (0.12) | 強調時の一瞬のズーム |
| `shake` | `start`, `duration`, `amplitude` (px), `frequency` | 少し拡大してから `crop` の x/y を減衰する sin 波で揺らす。1 カットに複数書ける |
| `flash` | `duration`, `color` | カット頭で白から明ける（`fade` の color 指定） |
| `fade_in` / `fade_out` | `duration`, `color`, `audio` | `fade` / `afade` |

ズーム系（zoom_in / zoom_out / kenburns / pan / punch_in）は 1 カットに 1 つまでです。shake・flash・fade は組み合わせて使えます。

**ワイプ（ピクチャー・イン・ピクチャー）** `pip`:

```json
"pip": { "source": "assets/wipe.mp4", "position": "br", "scale": 0.3, "start": 0.3, "end": 4.0,
         "border": 6, "border_color": "#FFFFFF", "margin": 0.04, "in": 0 }
```

`position` には `tl` / `tr` / `bl` / `br` / `center`、または `[x, y]`（中心位置の割合）を指定します。素材が短い場合は自動でループし、表示はフェードインします。

### 6.4 音（BGM の自動ダッキング・SE・音量）
- 全カットの「声」（ナレーション + 素材の元音声）を結合したものをサイドチェーンにして、BGM に `sidechaincompress` をかけます。声がある間だけ BGM が自動で下がります
- 実測（サンプル動画、0.5 秒窓）: **声がある区間は BGM が平均 -12.6 dB、声が無い区間は -0.5 dB**（ほぼそのまま）
- SE は `adelay` で正しい時刻に配置し、ダッキングの対象外として最後に `amix` します
- 最後に `loudnorm` を **2 パス**（1 パス目で計測して linear モードで適用）でかけて -14 LUFS に揃え、`alimiter` でクリップを防ぎます（サンプル実測: -14.0 LUFS / ピーク -1.0 dBFS）

### 6.5 構成支援
- `type: "hook"` で冒頭フックが 1 行で作れます。`title` は章区切りや CTA に使えます
- `target_duration: 90` を指定すると、合計の長さが目標とずれたときに警告します（`--dry-run` ならレンダリング前に確認できます）
- [`examples/template_90s.json`](examples/template_90s.json) に「フック → 課題 → 解決策 → デモ → 効果 → CTA」の 90 秒構成テンプレートがあります（ダミー素材でそのまま 90.03 秒にレンダリングできることを確認済み）。素材とテキストを差し替えて使ってください

## 7. テストレンダリング結果

実行日: 2026-11-01 / 環境: Linux, ffmpeg 6.1.1, Python 3.13, 4 コア

| カット表 | 結果 | 出力ファイル | 長さ | 解像度 | 所要時間 |
|---|---|---|---|---|---|
| `examples/sample_30s.json` | ✅ 成功 | `out/sample_30s.mp4` (9.9 MB) | **30.01 秒** | 1920x1080, 30fps | 約 65 秒 |
| `examples/sample_30s.json --aspect 9:16` | ✅ 成功 | `out/sample_30s_9x16.mp4` (7.9 MB) | **30.01 秒** | 1080x1920, 30fps | 約 83 秒 |
| `examples/sample_30s.csv --preview` | ✅ 成功 | `out/sample_30s_csv.mp4` (1.7 MB) | **30.01 秒** | 960x540, 30fps | 約 30 秒 |
| `examples/template_90s.json --preview` | ✅ 成功 | `out/challenge_90s.mp4` (5.0 MB) | **90.03 秒** | 960x540, 30fps | 約 82 秒 |

全出力のラウドネスは -14.0 LUFS（ebur128 で計測）でした。

実行ログ（`out/sample_30s.render.log`）:

```
INFO    プロジェクト: vedit sample (30s)  1920x1080 30fps  aspect=16:9  cuts=6
INFO      [1/6] hook           hook      4.00s  (6.3s)
INFO      [2/6] talk           normal    6.78s  (7.8s)  jumpcut 13.15s→6.78s
INFO      [3/6] photo          normal    5.00s  (7.2s)
INFO      [4/6] broll          normal    4.73s  (5.2s)  jumpcut 6.91s→4.38s
INFO      [5/6] pan            normal    5.00s  (6.4s)
INFO      [6/6] outro          title     4.50s  (4.5s)
INFO      最終合成 (テロップ/BGM/SE/ラウドネス) 23.9s
INFO    完了: out/sample_30s.mp4  (30.01s, 9.9 MB, 1920x1080)
```

ログとタイムラインの JSON は [`docs/render_logs/`](docs/render_logs/) に保存しています。MP4 本体は容量が大きく、§2 のコマンドで同じものを再生成できるため、リポジトリには入れていません（`out/` は `.gitignore` 済み）。代わりにコンタクトシート（冒頭の画像）を `docs/` に置いています。

サンプル 30 秒の構成:

| # | id | 時刻 | 内容 |
|---|---|---|---|
| 1 | hook | 0.00–4.00 | 冒頭フック: 白フラッシュ + 揺れ + ズーム + 巨大テロップのポップイン + インパクト SE |
| 2 | talk | 4.00–10.78 | ジャンプカット (13.15→6.78 秒)、`words` テロップ、ラベル帯、3.2 秒でパンチイン + 揺れ + ポップ SE |
| 3 | photo | 10.78–15.78 | 静止画の Ken Burns、`karaoke` テロップ、ナレーション、ウーッシュ SE |
| 4 | broll | 15.78–20.51 | カラーバー + ワイプ (PiP)、ナレーションの無音を詰める、テロップを 2 つ時間差で表示 |
| 5 | pan | 20.51–25.51 | 静止画の横パン、`slide_up` テロップ (一部の単語だけ色・サイズ変更) |
| 6 | outro | 25.51–30.01 | `title` タイプ、ズームアウト + フェードアウト |

## 8. 仕組み・ファイル構成

```
カット表 (JSON/CSV)
   │ vedit/cutlist.py   読み込み・既定値/プリセット/アスペクト別上書きの適用・検証
   ▼
カット計画 (vedit/segment.py: plan_cut)
   │ ffprobe で素材の長さを取得し、silencedetect で残す区間を決めて、各カットの長さを確定
   ▼
カットごとのセグメント生成 (ffmpeg × カット数)
   │ fit (cover/contain/blur) → ジャンプカット → zoompan → 揺れ → ワイプ → fade
   │ 音声 = 元音声 + ナレーション (48kHz ステレオ PCM)
   ▼
最終合成 (vedit/render.py, ffmpeg 1 回 + ラウドネス計測 1 回)
     concat → ass (テロップ焼き込み)
     声 ─┬────────────────────────────┐
         └→ sidechain → BGM (ループ/フェード) → amix ← SE (adelay)
                                                 → loudnorm (2 パス) → alimiter → MP4
```

```
vedit/
  __main__.py   CLI
  cutlist.py    カット表の読み込み (JSON/CSV)、組み込みスタイル・カットタイプ
  segment.py    カット単位の映像・音声フィルタグラフ生成、ジャンプカット
  telop.py      テロップ記法 → ASS 生成
  render.py     全体の処理、BGM ダッキング、SE、ラウドネス、ログ
  media.py      ffmpeg / ffprobe ラッパー、無音検出
tools/make_dummy_assets.py  ダミー素材生成
examples/       サンプルのカット表 (JSON / CSV / 90 秒テンプレート)
tests/          単体テスト
docs/           コンタクトシート、レンダリングログ
```

設計上のポイント:
- **再現性**: 入力（カット表と素材）が同じなら、同じコマンドで同じ動画になります。ランダム要素はありません（揺れも sin 波の決定的な式です）
- カットごとに中間ファイルを作るので、フィルタグラフが巨大にならず、エラーが起きてもどのカットが原因か分かります。中間ファイルは高画質 (CRF 14) + 無圧縮 PCM にしているので、最終エンコードで劣化が重なりません
- テロップの時刻は、ジャンプカット後に **実際に書き出したセグメントの長さ** を積み上げて決めるので、音とずれません
- `--verbose` で、実行したすべての ffmpeg コマンドを表示できます（手で再現・デバッグできます）

## 9. 素材と著作権について

- このリポジトリが生成するダミー素材（映像・画像・BGM・SE・ナレーション）は、すべて ffmpeg の内蔵ジェネレータ（`testsrc2`、`smptehdbars`、`mandelbrot`、`gradients`、`aevalsrc`、`anoisesrc`、`flite`）で**その場で合成**したものです。外部からは何もダウンロードしていません
- 本番の BGM や SE は、**ライセンスを確認した素材を別途配置する前提**です。`project.bgm.file` や `se[].file` のパスを差し替えてください。権利がはっきりしない素材は使わないでください
- 本番のナレーションは、自分で録音した音声や、利用規約上問題のない TTS の WAV/MP3 を `narration` に指定してください
