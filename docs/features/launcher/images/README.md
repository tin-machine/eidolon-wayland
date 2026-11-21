# Launcher UI concept assets — 生成記録

2026-11-21（日本時間）。組み込みの画像生成機能 `image_gen.imagegen` で生成・編集した UI コンセプト。実装画面ではない。

[画像ギャラリーと各案の説明](../spatial-launcher-ui-samples-20261121.md)を先に参照する。

## 保存画像

全画像は 1536 × 1024 の PNG。各ファイルは生成結果をそのまま保存した。P1 のみ後述の局所編集結果を採用した。

| ファイル | bytes |
| --- | ---: |
| [wrist-radial-host-library.png](wrist-radial-host-library.png) | 1,867,151 |
| [p1-summon-host-library.png](p1-summon-host-library.png) | 1,841,790 |
| [p2-pull-host-library.png](p2-pull-host-library.png) | 1,888,411 |
| [p3-pinned-host-library.png](p3-pinned-host-library.png) | 2,151,645 |
| [circle-hexagon-host-skins.png](circle-hexagon-host-skins.png) | 1,825,157 |

## 使用したプロンプト

新規生成は、以下の共通指示に改行と各画像の個別指示を連結して、画像ごとに一回ずつ実行した。
生成引数は `transparent_background: false`。既存画像の参照なし。CLI/API への切替は行っていない。

### 共通指示

```text
Create a polished, realistic 3D mixed-reality productivity UI concept illustration for EIDOLON. Landscape canvas approximately 1536x1024. First-person viewpoint in a quiet, neutral home office with a pale wall, desk, and soft daylight. Elegant soft pink, lavender, and mint accents on mostly opaque dark plum or warm cream menu surfaces. Crisp readable typography and generous spacing; restrained visual effects, no dense tiny prose. This is a concept, not a screenshot of existing software. Include the small, clean header "EIDOLON" and tag "CONCEPT UI". Use only fictitious hosts DEV-BOX and LAB-PI. Do not show IP addresses, usernames, credentials, official brand logos, or watermark. Avoid unreadable random text. Exact text and functional relationships matter more than decorative details. Every app library must have a persistent, clearly visible "All apps" navigation entry, a smaller "Favorites" entry, and a host filter control reading "All hosts". Show two clear large app cards with the same app title "Mousepad": one card has a visible mint host badge "DEV-BOX" and "ONLINE"; the other has a visible lavender host badge "LAB-PI" and "OFFLINE", visibly dimmed yet readable. Never merge these into a host-free app card. Favorites, when shown, are only one app. All large library panels exist independently in the room rather than attached to the user's hands. If a wrist launcher appears, it is a flat radial disc facing the user hovering beside the top of the wrist, never a bracelet encircling the arm, with exactly six simple symbolic actions in a consistent clockwise order from top: "Apps", "Motion", "Decor", "Close", "Recall", "Settings".
```

### 1. wrist-radial-host-library.png

```text
SCENE 1 — common wrist controls and detached app library. Compose a beautiful, uncluttered two-depth scene. In the lower left foreground, a natural left forearm extends gently upward and the small flat six-action radial disc floats above its wrist, facing the wearer. Each of its six sectors has one simple glyph and its short exact action label, with Apps gently highlighted in pink. In the upper right middle distance, a much larger rectangular app library floats independently at comfortable eye level, clearly separated by open air from the wrist. Library header EIDOLON / CONCEPT UI; the left navigation has All apps selected and Favorites below; the top right control is All hosts; the main region has the two required Mousepad cards with host badges and online/offline states. Only two cards, large readable text. A subtle small anchor glyph at the panel corner conveys spatial fixation. No connecting beams, no huge panel glued to the wrist. Show enough room context to make the separation unmistakable.
```

### 2. p1-summon-host-library.png

```text
SCENE 2 — P1 front summon shelf, with hands down and panel staying in the room. A broad, beautiful compact app shelf/library floats at eye level in front of the desk, visually anchored in the room. The wearer's two hands are lowered near the bottom of the composition resting comfortably near the desk, far away from the panel. No menus on these hands. The main panel dominates the view and contains the exact required All apps / Favorites navigation, All hosts filter, and two large Mousepad cards with DEV-BOX ONLINE and LAB-PI OFFLINE badges. Add a small top-level title "P1 · SUMMON" beside EIDOLON and CONCEPT UI. Below the panel is a very small favorites strip containing just one circular favorite button labelled "Mousepad" and "DEV-BOX". Leave visible empty space between this strip and lowered hands. The panel is clearly fixed in room space and not following an arm. No duplicates beyond the two library cards and one favorite, no busy grid. Calm realistic mixed reality visual.
```

#### 採用前の局所編集

生成された P1 を目視した後、同じ画像を編集対象に指定し、次の指示で不要な右下の radial だけを除去した。
`transparent_background: false` と一つのローカル画像参照を使用。元の生成結果は採用画像へ上書きせず別出力として保持した。

```text
Edit the supplied EIDOLON P1 SUMMON concept image with ONE tightly localized change: remove the entire circular radial action menu in the lower-right region of the image, including its six sectors, icons, text, circular border, central mark and glow. Reconstruct the neutral background wall, wooden desk, and desk mat naturally where that circular menu was. The result must show NO radial menu, wrist menu, or extra floating controls in this lower-right region. Preserve EVERYTHING ELSE exactly: the large main panel, EIDOLON title, P1 · SUMMON label, CONCEPT UI tag, All apps and Favorites navigation, All hosts filter, both Mousepad host cards (DEV-BOX ONLINE and dimmed LAB-PI OFFLINE), the centered small Favorites: Mousepad DEV-BOX strip, both lowered resting hands, furnishings, lighting, framing, colors, and typography. Do not alter, move, remove, retype, or redesign any of those preserved elements. This scene illustrates the full library staying fixed in the world after the wearer has lowered both hands. Do not add anything. Keep the exact same 1536x1024 landscape composition.
```

### 3. p2-pull-host-library.png

```text
SCENE 3 — P2 pull-out shelf interaction, two-frame concept board in one landscape image. Divide the canvas into TWO equally sized clear frames with a clean subtle vertical divider, same neutral room and palette in both. Small shared header EIDOLON / CONCEPT UI at top. Left frame titled "1 · PULL": in first-person view, left forearm at bottom left; a small flat six-action radial disc faces the user beside its wrist. The right hand pinches a tiny mint grab handle just outside the Apps sector and pulls a lightweight translucent rounded-rectangle OUTLINE away from the wrist into empty space. This temporary outline is mostly empty and light, not a full dense app catalog. Small label "All apps" on this temporary handle only. Right frame titled "2 · PLACE": the completed larger library is now fixed in space near eye level, with the user's hands lowered and detached. It has All apps selected, Favorites navigation, the All hosts filter, and the two required large Mousepad cards with DEV-BOX ONLINE and dimmed LAB-PI OFFLINE host badges. A small luminous app-shaped ghost just below the fixed shelf is labelled exactly "Mousepad · DEV-BOX". Use only one discreet arrow in the left frame to show outward motion. The key distinction must be obvious: pull only a lightweight handle/outline from the wrist, then interact with the full library after it is placed in the world. Keep text readable even in the two-frame layout.
```

### 4. p3-pinned-host-library.png

```text
SCENE 4 — P3 a library shelf pinned at a chosen side location, plus independent favorite launch buttons anywhere. First-person office view with spacious center and a library panel fixed at a comfortable LEFT-SIDE location beside the desk, angled gently toward the wearer. It must still be large enough to read. Main panel title "P3 · PINNED", header EIDOLON / CONCEPT UI. Panel has the persistent All apps entry, smaller Favorites entry, All hosts filter and the two required Mousepad host cards, DEV-BOX ONLINE and dimmed LAB-PI OFFLINE. A subtle anchor/pin glyph in panel corner conveys stable placement. Independently, one small circular favorite launch button floats near the right edge of the desk at the user's chosen reach point, clearly separate from the side panel. This favorite shows "Mousepad" and a readable "DEV-BOX" host badge. Beside it is a distinct small persistent launcher button labelled "All apps", providing a complete-library route from that location. A natural right hand is placing a translucent lightweight app ghost at a different empty point above the desk, label exactly "Mousepad · DEV-BOX"; this is an unlaunched preview, not a running window. Keep the room calm and uncluttered; the small shortcuts are a subset, not the entire app catalog. No wrist menu needed in this image. No additional active app windows.
```

### 5. circle-hexagon-host-skins.png

```text
SCENE 5 — controlled appearance-only theme comparison. One landscape design-review concept board with two equal side-by-side views against the same soft neutral MR room background. Shared header EIDOLON / CONCEPT UI, very small subtitle "Same actions · Same hosts". Left view labelled "CIRCLE"; right view labelled "HEXAGON". Both views contain the same six-action flat radial launcher, same orientation and exact clockwise action order Apps, Motion, Decor, Close, Recall, Settings. On the left the six action tiles and app token silhouettes are CIRCULAR; on the right those corresponding tiles/token silhouettes are HEXAGONAL. Use exactly the same colors, labels, content, scale, positions, and selected state. Beneath each radial launcher show an identical compact app shelf/library, with persistent All apps entry, smaller Favorites entry, All hosts filter and two clearly readable Mousepad cards: DEV-BOX ONLINE and dimmed LAB-PI OFFLINE. Host badges stay rectangular and legible in both themes, never removed or replaced by color alone. Theme affects just visual silhouettes and surface styling; identical actions and host choice remain obvious. Do not add interaction differences, extra items, alternate order, or a different host set. Leave generous space, no human hands needed for this comparative view.
```

## 目視確認

全 5 枚で `All apps` / `Favorites` / `All hosts` と、同名アプリの `DEV-BOX` / `LAB-PI` を確認した。
P1 の手を下ろした状態、P2 の outline → 配置、P3 の独立ボタン、テーマ比較で同じ六操作が保たれることを確認した。
画像中の文字や寸法は実装契約を置き換えない。可読性・誤選択・手入力・性能は Quest 上で別途評価する。
