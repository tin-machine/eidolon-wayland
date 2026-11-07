# NX18 B2: APK UIDから保存済みLinux profileへの固定SSH probe

NX18aのv2保存契約とB1のtransportを、debug APK専用の固定検査へ接続する。通常のTermux v1 UI・設定保存・接続経路は維持する。Linux GUI、catalog、app起動、forward、helper SHMは今回の完了範囲に含めない。実APKでの受入れは統合APK配備後に記録する。

## APIと所有範囲

- `RuntimeConfig.debug_probe_profile_v2` はstrict v2 JSON文字列。nested JSONの重複keyを`serde_json::Value`化で失わず、modelの`Profile::parse`へ渡す。profile IDは`linux-probe`、backendは`linux_ssh`に固定する。通常`profiles`は変更しない。
- `connect_saved_probe` commandはschemaとrequest IDだけを受ける。endpoint/pin/username/revision/bindingはruntime作成時のimmutable `ConnectSpec`から使う。DNS保存契約は残すが、本probeはB1と同じくIPv4 literalのみ。DNSをloopbackへfallbackしない。
- `CredentialRequired.profile_id`はtransportで実際にcaptureした値。bindingを通常snapshot/eventへ複製せず、`nativeCredentialScope(handle)`のprivate envelopeから取得する。scopeはsession/request/profile/revision/bindingを含む。文字列IDをDoubleへ変換しない。
- Hostの`provideDebugProbeCredential`は`withStoppedRuntime`と同じworkerから同期呼出しする。`ProbeCredentialCapture`でruntimeとconnect request、scopeの全identityを照合し、`CredentialStore.withPrivateKey`から`nativeProvideCredentialFor`へ一度だけ渡す。key bufferは既存契約どおりfinallyで消去する。失敗後の自動再供給なし。
- coreのcredential slotとtransportの現在scopeの両方を照合する。旧unscoped `nativeProvideCredential`はv2 scopeを拒否する。JNI受付のACCEPTEDはSSH認証・固定probe成功とは区別し、後続の`ProbeReady`まで待つ。

## debug/release境界

実際の`scripts/build-native`はdebug時だけ`eidolon-core/debug-fixtures`を指定する。このfeatureがないcoreは新profile config、新command、private credential scope/供給APIを拒否する。`cfg(debug_assertions)`だけには依存しない。featureなしlibraryを使うintegration testは、debug assertionsが有効なhost test profileでも拒否されることを確認する。

profile store、receiver、実行caseは`src/debug`だけに置く。release Manifestにreceiverは追加しない。JNIの安定symbol自体は残り、coreからINVALID_ARGUMENT/nullを返す。

## 正規保存と固定導入手順

保存はアプリの`noBackupFilesDir/remote-probe/`内に限定する。Androidの[Context.getNoBackupFilesDir](https://developer.android.com/reference/android/content/Context#getNoBackupFilesDir())は追加権限なしで使えるアプリ専用領域で、自動backup対象外。file操作には公開[android.system.Os API](https://developer.android.com/reference/android/system/Os)を使う。

1. operatorが接続先、port、user、Ed25519 host key、Eidolon installation ID、SSH側authorized keyを確認する。端末の公開identityを再利用し、秘密鍵は端末から取り出さない。host keyの未確認値を自動採用しない。
2. 確認済みv2 profileを同APK UIDの固定 `no_backup/remote-probe/linux-probe.pending.json` へ事前配置する。directory 0700、regular file 0600、link数1。初回revisionは文字列`"1"`。private作業ログには配置元hash/改訂と照合結果を記録し、公開文書へendpoint/key materialは記載しない。
3. 固定caseを明示起動する。この操作がstaging profileのimport確認に相当する。既存v2と全fieldが一致すればそのまま使用し、変更時は既存bindingをcaptureしてrevision+1のみ受理する。旧Termux v1 profileは自動移行・上書きしない。
4. storeはUTF-8/16KiB/型/重複keyをstrict検証し、O_NOFOLLOW・same UID・mode・regular fileを確認する。O_EXCLの自分のtmpへ書込み、file fsync、strict読戻し、rename、directory fsync、保存後読戻しを行う。残っていたtmpは黙って削除せず拒否する。
5. import済みprofileをcaptureして独立probe runtimeを作る。その`paths.files_dir`は同private領域の固定`runtime/`。旧Termux journalを新endpointのreconcileに渡さない。通常設定と旧journalは停止後/検査後のdigest一致をboolで記録し、値/hash本文を診断へ出さない。

ADB intentで指定できる値は次のcaseと32桁hex nonceだけ。host/path/argv/JSON/profile IDのextraは拒否する。receiverの呼出しにはDUMP権限が必要。

```sh
adb -s "$EIDOLON_QUEST_SERIAL" shell am broadcast \
  -n io.github.tinmachine.eidolon/.LinuxSshProbeReceiver \
  --es case linux-ssh-probe --es nonce "$NONCE"

# 同じnonceの明示停止。再接続はせず、自分のruntimeだけを回収する。
adb -s "$EIDOLON_QUEST_SERIAL" shell am broadcast \
  -n io.github.tinmachine.eidolon/.LinuxSshProbeReceiver \
  --es case linux-ssh-probe-stop --es nonce "$NONCE"
```

## 合格条件と失敗時の記録

通常runtimeを停止したHost単一workerで実行する。接続は30秒の全体deadline。credential供給を同workerへ再enqueueして待つことはしない。SSHの実行内容はB1の固定`id -u`と固定token確認だけ。

合格には全項目が必要。

- captureしたconnect requestにcommand ACKがあり、認証要求は1回だけ、private scopeが完全一致する。
- 同session/profile/revisionの`ProbeReady`、固定token一致、EOF、exit status 0。単なるJNI ACCEPTEDでは通さない。
- captured sessionへdisconnectを送り、対応ACK・Disconnected・owned connection 0・private scope消去を確認する。
- Stop/destroy後に旧handle STALE、registryのruntime/worker 0を確認する。
- 通常設定/旧journalが不変で、別handleの通常runtimeがReadyかつtransport Idleへ復帰する。

失敗/cancel時にも自分のprobe runtimeをStop/destroyする。10秒で回収できなければ`cleanup_unconfirmed`とし、`canRestart=false`でHostがownerを保持し、通常runtimeの再作成と他の排他case開始を抑止する。active ownerとhandleを保持し、同nonceの明示stopで回収だけを再試行できる。scope/credentialを再送しない。通常UI復帰のboolと実接続のboolは独立して記録する。`nativeCreate`失敗/0返却時もregistryのruntime/workerが厳密に0か確認し、ownerなしの場合は通常runtime復帰を許可する。確認不能/別ownerありなら抑止を保持し、同nonce stopで再照合する。

同時実行は1件、nonce replay履歴は直近64件。結果は固定reason token、UID、bool、handle等のmetadataだけ。private scope envelope、接続設定、鍵、任意remote stdout/stderrは保存しない。これは物理HMD入力やLinux GUIの受入れを示さない。

## 検証記録

実装時の検証結果:

- pure JVM 11件PASS（既存v2契約5、strict codec/capture/回収owner gate 6）。JSON型変換、重複decoded key、UTF-8破損、補助文字、孤立surrogate、16KiB、大きなrevision文字列、失効runtime、誤scope、二重供給、別ownerからの回収解除を含む。
- 新debug case/store、JNI Kotlin宣言、Host同期credential供給部分をAndroid API 36 jarでstub compileしてPASS。Android側の`O_DIRECTORY`は公開API定数にないため、公開`open`後の`fstat`でdirectory種別を確認する形に修正した。
- model全57件PASS。runtime限定13件PASS（v2への旧unscoped供給拒否・private binding非流出・停止後破棄を含む）。
- `debug-fixtures`なしの実core libraryを使用するintegration 1件PASS。debug assertionsが有効でも新config/command/private scope/provide_forを拒否する。
- core/androidのhost all-targets clippy `-D warnings`、rustfmt、diff check PASS。固定vendor Smithayの既存warningは変更していない。

これはAndroidの最終APK compileや実SSH接続の合格ではない。JNI Android target、同UID file systemでのatomic保存、鍵供給、ProbeReady、切断/回収、通常runtime復帰はrootの統合buildとQuest検証が必要。ホストの検査で実APK UIDの認証が成功したとは報告しない。
