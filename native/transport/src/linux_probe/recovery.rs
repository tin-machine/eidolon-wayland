//! 明示された同一scopeのreceipt照会だけ。保存PIDにsignalせず、未知ownerを削除しない。
use super::{Pins, hex, journal, owner, protocol};
use eidolon_model::ids::SessionId;
use russh::{ChannelMsg, client};
use serde::Serialize;
use std::path::Path;
use tokio::time::{Duration, timeout};
use tokio_util::sync::CancellationToken;

pub struct Recovery<'a> {
    pub session: SessionId,
    pub profile_binding: &'a str,
    pub profile_revision: &'a str,
    pub files: &'a Path,
    pub nonce: &'a str,
    pub uid: u32,
    pub pins: &'a Pins,
    pub gtk: Option<&'a super::GtkBinding>,
}
#[derive(Clone, Debug, Serialize)]
pub struct RecoveryReport {
    pub session: SessionId,
    pub origin_session: Option<SessionId>,
    pub nonce: String,
    pub uid: u32,
    pub queried: bool,
    pub pending: bool,
    pub receipt_verified: bool,
    pub channel_closed: bool,
    pub error: Option<&'static str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub gtk_case: Option<super::GtkCase>,
}

pub async fn recover<H: client::Handler>(
    client: &client::Handle<H>,
    request: Recovery<'_>,
    cancel: &CancellationToken,
) -> RecoveryReport {
    let mut report = RecoveryReport {
        session: request.session,
        origin_session: None,
        nonce: request.nonce.into(),
        uid: request.uid,
        queried: false,
        pending: true,
        receipt_verified: false,
        channel_closed: false,
        error: None,
        gtk_case: request.gtk.map(|gtk| gtk.case),
    };
    let result=async {
        if request.uid==0||!hex(request.profile_binding,64)||!crate::forward_probe::valid_nonce(request.nonce)||!request.pins.valid()||request.gtk.is_some_and(|gtk|!gtk.valid(request.pins))||request.profile_revision.parse::<u64>().is_err()||request.profile_revision.starts_with('0')||!request.profile_revision.bytes().all(|b|b.is_ascii_digit()) {return Err("INVALID_ARGUMENT");}
        if cancel.is_cancelled(){return Err("CANCELLED");}
        let Some((mut journal,origin))=journal::Journal::matching(&request)? else{report.pending=false;return Ok(());};
        report.origin_session=Some(origin);
        let command=if request.gtk.is_some(){owner::gtk_command(request.pins,request.nonce,request.gtk,true)?}else{owner::cleanup_command(request.pins,request.nonce)?};
        let mut channel=tokio::select!{biased;_=cancel.cancelled()=>return Err("CANCELLED"),v=timeout(Duration::from_secs(5),client.channel_open_session())=>v}.map_err(|_|"LINUX_AUDIT_TIMEOUT")?.map_err(|_|"SSH_IO_FAILED")?;
        let exchange=async {
            if cancel.is_cancelled(){return Err("CANCELLED");}
            report.queried=true;
            channel.exec(true,command).await.map_err(|_|"SSH_IO_FAILED")?;
            let(mut bytes,mut stderr,mut eof,mut status,mut signal)=(Vec::new(),0usize,false,None,false);
            while let Some(message)=channel.wait().await {
                match message {
                    ChannelMsg::Data{data}=>{if bytes.len()+data.len()>16384{return Err("LINUX_AUDIT_INVALID");}bytes.extend(data);},
                    ChannelMsg::ExtendedData{data,ext:1}=>{stderr=stderr.saturating_add(data.len());if stderr>16384{return Err("LINUX_AUDIT_INVALID");}},
                    ChannelMsg::Eof=>eof=true,
                    ChannelMsg::ExitStatus{..} if status.is_some()=>return Err("LINUX_AUDIT_INVALID"),
                    ChannelMsg::ExitStatus{exit_status}=>status=Some(exit_status),
                    ChannelMsg::ExitSignal{..}=>signal=true,
                    ChannelMsg::Close=>break,
                    ChannelMsg::Failure|ChannelMsg::ExtendedData{..}=>return Err("LINUX_AUDIT_INVALID"),
                    _=>(),
                }
            }
            report.channel_closed=true;
            if !eof||status!=Some(0)||signal{return Err("LINUX_REMOTE_EXIT");}
            if let Some(gtk)=request.gtk {super::gtk::audit(&bytes,request.nonce,request.uid,gtk)}else{protocol::audit(&bytes,request.nonce,request.uid,request.pins)}
        };
        let outcome=tokio::select!{biased;_=cancel.cancelled()=>Err("CANCELLED"),v=timeout(Duration::from_secs(10),exchange)=>v.map_err(|_|"LINUX_AUDIT_TIMEOUT").and_then(|v|v)};
        report.channel_closed|=matches!(timeout(Duration::from_secs(1),channel.close()).await,Ok(Ok(())));
        report.receipt_verified=outcome?;
        if !report.receipt_verified{return Err("LINUX_CLEANUP_PENDING");}
        if !report.channel_closed{return Err("LINUX_CHANNEL_PENDING");}
        if cancel.is_cancelled(){return Err("CANCELLED");}
        journal.clear()?;report.pending=false;Ok(())
    }.await;
    report.error = result.err();
    report
}
