# 02 — 配置solverと曲面の数学

[入口](README.md) / [契約](01-contracts.md)。実装先は `model/workspace_layout/geometry.rs`。計算はf64、PanelPose生成時だけf32。epsilonを大きくして不適切な配置を通さない。

## 1. 基準系と単位

X+右、Y+上、Z+正面。Panel local前面は-Z。回転はxyzw、SDK constructorの並び順の変換は既存PanelPoseCodecだけで行う。

```text
yaw(θ) quaternion = [0, sin(θ/2), 0, cos(θ/2)]
rotate_y([x,y,z],θ) = [cosθ*x + sinθ*z, y, -sinθ*x + cosθ*z]
```

内部角度はrad。wire/UIだけdeg。`deg * PI / 180`は一度だけ。yawは[-PI,PI)へ正規化。qと-qは等価で、fixture照合も両符号を許す。出力は原則w>=0にcanonical化する（w=0はy>=0）。

capture.poseのnormが現行PanelPose::validを満たし、abs(q.x),abs(q.z)<=1e-4ならyaw-onlyとして受理する。それ以外はCW_REFERENCE_TILTED。入力quaternionを勝手に水平化・normalizeして無効入力を受理しない。yaw抽出に用いるw/yはvalid確認後にnormを揃える。

中央の適用済み位置P0、yaw Q、距離dから:

```text
E = P0 - rotate_y([0,0,d], Q)
P(θ,h) = E + rotate_y([d*sinθ, h, d*cosθ], Q)
q(θ) = yaw(Q+θ)
```

θ_left<0、θ_right>0。中央θ=0,h=0。初版はh=0のみ。

## 2. Envelopeを取得する

`model/window_slots.rs::Envelope::for_window` と `with_canvas` を使い、本文physical size、現在のWindowControls、装飾canvas/content_originを含むL/R/B/Tを作る。既存式をコピーしない。座標は**本文中央を原点とするメートル値**。L<0<R、B<0<T、全値finiteを要求。

captureはdimension、content origin、theme revision、controls config、target px、physical sizeの値を保持し、操作中にいずれか変わればCW_GEOMETRY_CHANGED。content_frame/ContentSeqの増加だけは無視する。window borderや透明装飾の占有も初版では保守的に予約する。

画像canvasのlocal offsetを失い、単純にwidth=max(content,canvas)へ置換しない。非対称controlsのためL=-Rを仮定しない。

## 3. 初版radial_threeの厳密アルゴリズム

各Envelopeについて `a_i^- = atan2(L_i,d)`、`a_i^+ = atan2(R_i,d)`。gamma=gap_deg*PI/180。

```text
theta[center] = 0
theta[right] = a_center_plus - a_right_minus + gamma
theta[left]  = a_center_minus - a_left_plus - gamma
pose[i] = P(theta[i],0), q(theta[i])
```

根拠: radial Flatの任意のlocal x点の方位角は `theta + atan2(x,d)`。上式は `right.low - center.high = gamma` と `center.low - left.high = gamma` を直接満たす。

```text
solve_three(capture, params):
  validate_count_scope_roles_and_params()
  validate_reference_pose_yaw_only()
  read_actual_envelopes()
  derive_E_and_Q_from_center()
  compute_three_thetas()
  compute_all_f64_poses()
  validate_all_angular_intervals()
  convert_all_to_f32_and_check_PanelPose_valid()
  recheck_intervals_from_converted_poses_with_1e-6_rad_rounding_tolerance()
  return Proposal_or_single_error
```

角度区間は各 `[theta+a_minus, theta+a_plus]`。全区間が[-85°,85°]に収まること。interval間のgapは `>= requested_gap - 1e-6 rad`。全poseは現行範囲X/Z[-10,10]m、Y[0,4]m内。1件でも不可ならCW_NO_FIT。距離・yaw・サイズのclamp、他windowの削除、gammaの自動縮小をしない。

初版のwindow数はちょうど3のため、選ばれていない通常windowへの遮蔽はない。操作用UIは適用開始時に閉じる。SDK検査rayが別のUIに遮られた場合は成功ACKを出さず、時間内に解消しなければ失敗する。

この判定は基準点Eからの占有保証。頭をずらした位置での見え方の保証とは異なる。head追従や自動再配置はしない。

## 4. 数値golden

[geometry-v1.json](fixtures/geometry-v1.json)は独立に計算した設計値。初版コードで期待値を再生成してテストを通さない。

本文Envelope[-0.6,0.6,-0.45,0.45]、P0=[0,1.5,1.5]、Q=0、d=1.5、gap3°なら:

```text
theta_left  = -0.8133726317845595 rad = -46.602818972703616 deg
theta_right = +0.8133726317845595 rad
left.position  = [-1.089912712561722,1.5,1.0305776433614056]
right.position = [+1.089912712561722,1.5,1.0305776433614056]
left.q  = [0,-0.3955680963873748,0,0.9184366505755683]
right.q = [0,+0.3955680963873748,0,0.9184366505755683]
```

実theme Envelopeでは角度が変わるのでこの値を製品定数にしない。d=1.0の同幅3枚は全約191.78°のため初版capでCW_NO_FIT。上位計画の表を見て「1.0mは必ず成立」と解釈しない。

非対称例: center L=-0.6,R=0.8、left L=-0.7,R=0.5、right L=-0.4,R=0.9、d=1.5,gap3°では theta_left=-0.7546168090688369、theta_right=0.8029195955608991。

## 5. Phase 2: Flat/Fan/Arc/Customの一般化

role優先順を `center,right,left,up,down,up_left,up_right,down_left,down_right` に固定。同じruntimeで割り当てたroleはwindow消失まで動かさない。新窓に既存slotを奪わせない。

### Flat

reference frameの平面z=d、yaw=0相対。中央x=0。各rowは中心→右→左の順で作る。隣接するi,jについて `x_j = x_i + R_i - L_j + gap_m`、左方向は対称な式。gap_m=`d*tan(gap_rad)`。上/下段は各rowのEnvelope上下端から最低同gap_mを確保。全poseと実角度区間を最後に検証する。

### Fan

各窓の現positionを維持し、E基準frameのhorizontal x,zから `theta=atan2(x,z)` を求める。horizontal距離 `d_i=hypot(x,z)`、yaw=Q+theta、縦位置は維持。z<=0やE直上など不定な姿勢は拒否。回転後のEnvelope角度区間でoverlapを再判定し、重なった場合はそのままCW_NO_FIT。Fan操作で勝手に中心位置を変更しない。

### Arc

水平rowごとに3節の非対称式を使い、半径dとyawを揃える。中央row h=0。上下rowはyawだけ回し、pitchは0相対のままにする。「上段の法線まで眼を完全に向く」とは主張しない。

垂直gapの計算にcornerだけでは足りない。各Panelのelevation extremaは、x=L,R、およびL<=0<=Rならx=0、y=B,Tの候補点で求める:

```text
elevation(x,y,h) = atan2(h+y, sqrt(d*d+x*x))
```

中央rowの最大elevation=bmax、最小=bmin。上下gap gamma_vは3°固定から開始。各上段panel iで rho_max=sqrt(d²+max(L_i²,R_i²)) として:

```text
h_up   = max_i(rho_max_i * tan(bmax+gamma_v) - B_i)
h_down = min_i(rho_max_i * tan(bmin-gamma_v) - T_i)
```

角度が±90°へ達する、非finite、pose範囲外、全垂直span>120°なら拒否。上/下rowそれぞれ共通hを使う。rowの候補数が0なら計算しない。全rowの実角度箱を再検査。既定寸法で9枚必ず入るとは保証しない。

### Custom / pinned

既存base poseを変更しない。明示的に選んだmanual overrideを含む再配置だけが変更可能。自動fillでは既存窓を障害物として固定し、空roleに新窓だけを提案する。既存rowのhや隣接thetaを変える必要があればauto-fillを断念し、通常新窓は既存slot defaultのまま・「自動配置不可」と表示する。defaultが他面を遮蔽する場合はその新窓の入力を有効にせず、利用者に手動配置を要求する。

## 6. 重なり判定の精度

radial Flatのhorizontalは上式で厳密、verticalは上記の極値候補で保守的。非radial/customの障害物はreference frameへ全矩形を変換し、辺上の方位・仰角極値も評価する。最初の一般化でこれを実装しない場合、custom障害物と自動fillの併用をCW_NO_FITとして拒否する。4cornerだけの近似を一般姿勢の厳密判定だと扱わない。

物理面の交差、基準点からの遮蔽、描画順は別問題。Arcの角度箱がdisjointなら基準点のrayは同時に2本文へ入らない。別視点・移動中・装飾の透明部については04/07のSDK試験を行う。

## 7. Cylinder数学

中央をlocal原点、円筒の中心軸は(0,y,-R)。弧長S、高さH:

```text
phi=(u-0.5)*S/R
F(u,v)=[R*sin(phi), H*(0.5-v), R*(cos(phi)-1)]
Theta=S/R
chord=2*R*sin(Theta/2)
sagitta=R*(1-cos(Theta/2))
```

これはEidolonの標準モデルでありSDK pivotを推測しない。SDKへの剛体変換は06のPoCで決める。u=0左、v=0上。入力は0<=u,v<1、描画mesh生成では閉区間端1も使用する。

world ray O+tDを逆poseでlocalへ。円筒交差は:

```text
a=Dx²+Dz²
b=2*(Ox*Dx+(Oz+R)*Dz)
c=Ox²+(Oz+R)²-R²
Dscr=b²-4*a*c
```

a<=1e-12、非finite、Dscr<-1e-12はmiss。[-1e-12,0)だけ0へ丸める。安定な二次方程式解法を使い、二つの候補それぞれにt>=0、frontface、弧角、height、input範囲を評価してから最近を選ぶ。前面法線は `n=[-sin(phi),0,-cos(phi)]`、`dot(n,D)<-1e-8` を要求。逆UVは `phi=atan2(x,z+R); u=0.5+R*phi/S; v=0.5-y/H`。範囲外をclampしない。

R→∞の検査は入力にinfinityを許す意味ではない。R=1000などfinite値でFlatへの収束を確認する。製品のradius上限は06で別に固定する。

## 8. 可読性と負荷は数式だけで合格にしない

上位計画の1.0/1.5/2.0m、文字角度、首の移動、copy量の表を保持する。geometry fixtureはSDK表示や光学の測定ではない。距離を遠ざけて同じpixel/physical幅を使えば文字は小さく見える。曲率変更だけでlogical resize・font設定・texture解像度を変えない。
