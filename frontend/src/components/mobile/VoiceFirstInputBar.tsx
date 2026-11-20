'use client';

import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  AudioLines,
  Captions,
  CaptionsOff,
  Mic,
  Plus,
  Send,
  Volume2,
  VolumeX,
  X,
} from 'lucide-react';
import { apiFetch } from '@/utils/api';

interface VoiceFirstInputBarProps {
  expertId?: string;
  kbId?: string;
  disabled?: boolean;
  sceneMode: 'agent' | 'chat';
  onChangeSceneMode: (mode: 'agent' | 'chat') => void;
  latestAssistantReply?: string;
  onSubmit: (text: string) => void;
  onOpenSnap?: () => void;
}

type DragGesture = 'idle' | 'recording' | 'cancel' | 'edit';

/**
 * 移动端 Gemini 风格悬浮胶囊输入栏 + 单手语音直达 + 全屏沉浸语音模式
 * - 上方紧凑 [Chat | Task] 模式切换胶囊
 * - 底部悬浮胶囊：左侧 + 传图/附件、中间自适应输入框、右侧麦克风与声波全屏语音（有文本时切换翡翠绿发送按钮）
 */
export function VoiceFirstInputBar({
  expertId,
  kbId,
  disabled = false,
  sceneMode,
  onChangeSceneMode,
  latestAssistantReply = '',
  onSubmit,
  onOpenSnap,
}: VoiceFirstInputBarProps) {
  const [textValue, setTextValue] = useState('');
  const [gesture, setGesture] = useState<DragGesture>('idle');
  const [isTranscribing, setIsTranscribing] = useState(false);
  const [toastMsg, setToastMsg] = useState<string | null>(null);
  const [durationSec, setDurationSec] = useState(0);

  // 全屏沉浸语音模式状态
  const [voiceOverlayOpen, setVoiceOverlayOpen] = useState(false);
  const [showSubtitles, setShowSubtitles] = useState(true);
  const [voiceMuted, setVoiceMuted] = useState(false);
  const [lastTranscript, setLastTranscript] = useState('');

  const startPosRef = useRef<{ x: number; y: number } | null>(null);
  const startTimeRef = useRef<number>(0);
  const gestureRef = useRef<DragGesture>('idle');
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const streamRef = useRef<MediaStream | null>(null);
  const timerRef = useRef<number | null>(null);

  const showToast = useCallback((msg: string) => {
    setToastMsg(msg);
    window.setTimeout(() => {
      setToastMsg((prev) => (prev === msg ? null : prev));
    }, 2600);
  }, []);

  const stopMediaTracks = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearInterval(timerRef.current);
      timerRef.current = null;
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
    }
  }, []);

  useEffect(() => {
    return () => {
      stopMediaTracks();
    };
  }, [stopMediaTracks]);

  const tryBrowserSpeechFallback = useCallback(
    (editAfterRecognize: boolean) => {
      const win = window as unknown as {
        SpeechRecognition?: new () => {
          lang: string;
          interimResults: boolean;
          maxAlternatives: number;
          onresult: ((e: { results: ArrayLike<ArrayLike<{ transcript: string }>> }) => void) | null;
          onerror: (() => void) | null;
          start: () => void;
        };
        webkitSpeechRecognition?: new () => {
          lang: string;
          interimResults: boolean;
          maxAlternatives: number;
          onresult: ((e: { results: ArrayLike<ArrayLike<{ transcript: string }>> }) => void) | null;
          onerror: (() => void) | null;
          start: () => void;
        };
      };
      const RecognitionCtor = win.SpeechRecognition || win.webkitSpeechRecognition;
      if (!RecognitionCtor) {
        showToast('本地语音引擎未配置，请直接键入提问');
        return;
      }
      try {
        const rec = new RecognitionCtor();
        rec.lang = 'zh-CN';
        rec.interimResults = false;
        rec.maxAlternatives = 1;
        rec.onresult = (event) => {
          const transcript = event.results?.[0]?.[0]?.transcript?.trim();
          if (!transcript) return;
          setLastTranscript(transcript);
          if (editAfterRecognize) {
            setTextValue(transcript);
          } else {
            onSubmit(transcript);
          }
        };
        rec.onerror = () => {
          showToast('浏览器语音识别不可用，请改用文字输入');
        };
        rec.start();
      } catch {
        showToast('语音识别启动失败，请改用文字输入');
      }
    },
    [onSubmit, showToast]
  );

  const uploadAndTranscribe = useCallback(
    async (blob: Blob, editAfterRecognize: boolean) => {
      setIsTranscribing(true);
      try {
        const fd = new FormData();
        fd.append('file', blob, 'voice_input.webm');
        if (expertId) fd.append('expert_id', expertId);
        if (kbId) fd.append('kb_id', kbId);

        const res = await apiFetch('/api/v1/mobile/voice-query', {
          method: 'POST',
          body: fd,
        });

        if (!res.ok) {
          throw new Error('语音接口返回异常');
        }
        const data = await res.json();
        const transcript = String(data?.transcript || '').trim();
        if (!transcript) {
          if (data?.fallback === 'browser_speech_or_text') {
            tryBrowserSpeechFallback(editAfterRecognize);
          } else {
            showToast('未识别到清晰语音，请重试');
          }
          return;
        }

        setLastTranscript(transcript);
        if (editAfterRecognize) {
          setTextValue(transcript);
        } else {
          onSubmit(transcript);
        }
      } catch {
        tryBrowserSpeechFallback(editAfterRecognize);
      } finally {
        setIsTranscribing(false);
      }
    },
    [expertId, kbId, onSubmit, showToast, tryBrowserSpeechFallback]
  );

  const startRecordingSession = async () => {
    if (disabled || isTranscribing) return;
    if (typeof navigator === 'undefined' || !navigator.mediaDevices?.getUserMedia) {
      tryBrowserSpeechFallback(false);
      return;
    }

    startTimeRef.current = Date.now();
    gestureRef.current = 'recording';
    setGesture('recording');
    setDurationSec(0);

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;

      const mimeType = MediaRecorder.isTypeSupported('audio/webm') ? 'audio/webm' : '';
      const recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
      mediaRecorderRef.current = recorder;
      audioChunksRef.current = [];

      recorder.ondataavailable = (ev) => {
        if (ev.data.size > 0) {
          audioChunksRef.current.push(ev.data);
        }
      };

      recorder.start();
      timerRef.current = window.setInterval(() => {
        setDurationSec((s) => s + 1);
      }, 1000);
    } catch {
      stopMediaTracks();
      gestureRef.current = 'idle';
      setGesture('idle');
      showToast('无法访问麦克风，请检查浏览器权限');
    }
  };

  const handlePointerDown = async (e: React.PointerEvent<HTMLButtonElement>) => {
    if (disabled || isTranscribing) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    startPosRef.current = { x: e.clientX, y: e.clientY };
    await startRecordingSession();
  };

  const handlePointerMove = (e: React.PointerEvent<HTMLButtonElement>) => {
    if (gestureRef.current === 'idle' || !startPosRef.current) return;
    const dx = e.clientX - startPosRef.current.x;
    const dy = e.clientY - startPosRef.current.y;

    let nextGesture: DragGesture = 'recording';
    if (dy < -55) {
      nextGesture = 'cancel';
    } else if (dx > 55) {
      nextGesture = 'edit';
    }
    if (nextGesture !== gestureRef.current) {
      gestureRef.current = nextGesture;
      setGesture(nextGesture);
    }
  };

  const finalizeRecording = () => {
    if (gestureRef.current === 'idle') return;
    const finalGesture = gestureRef.current;
    const elapsedMs = Date.now() - startTimeRef.current;

    gestureRef.current = 'idle';
    setGesture('idle');
    startPosRef.current = null;

    const recorder = mediaRecorderRef.current;
    if (!recorder || recorder.state === 'inactive') {
      stopMediaTracks();
      return;
    }

    recorder.onstop = () => {
      stopMediaTracks();
      const chunks = audioChunksRef.current;
      audioChunksRef.current = [];

      if (finalGesture === 'cancel') {
        showToast('已取消语音发送');
        return;
      }
      if (elapsedMs < 400) {
        showToast('按住麦克风说话，松开即发送');
        return;
      }
      if (chunks.length === 0) return;

      const blob = new Blob(chunks, { type: recorder.mimeType || 'audio/webm' });
      void uploadAndTranscribe(blob, finalGesture === 'edit');
    };

    try {
      recorder.stop();
    } catch {
      stopMediaTracks();
    }
  };

  const handleSendText = () => {
    const trimmed = textValue.trim();
    if (!trimmed || disabled) return;
    onSubmit(trimmed);
    setTextValue('');
  };

  const hasText = textValue.trim().length > 0;

  return (
    <div className="relative bg-background px-3 pb-3 pt-1.5">
      {toastMsg && (
        <div className="pointer-events-none absolute -top-11 left-1/2 z-30 -translate-x-1/2 whitespace-nowrap rounded-full border border-border bg-surface-1 px-4 py-1.5 text-xs font-medium text-foreground shadow-lg">
          {toastMsg}
        </div>
      )}

      {/* 按住说话手势浮层 */}
      {gesture !== 'idle' && !voiceOverlayOpen && (
        <div className="fixed inset-0 z-40 flex flex-col items-center justify-end bg-black/55 pb-28 backdrop-blur-[2px]">
          <div className="w-[82%] max-w-xs rounded-3xl border border-border bg-surface-1 p-6 text-center shadow-2xl">
            <div className="mx-auto mb-3 flex h-14 w-14 items-center justify-center rounded-full bg-surface-2">
              {gesture === 'cancel' ? (
                <X className="h-6 w-6 text-rose-500" strokeWidth={1.75} />
              ) : (
                <Mic className="h-6 w-6 text-emerald-500" strokeWidth={1.75} />
              )}
            </div>
            <p className="text-base font-semibold text-foreground">
              {gesture === 'cancel'
                ? '松开手指，取消发送'
                : gesture === 'edit'
                ? '松开手指，转为文字编辑'
                : '正在聆听，松开即发送'}
            </p>
            <p className="mt-1.5 font-mono text-sm tabular-nums text-muted-foreground">
              {String(Math.floor(durationSec / 60)).padStart(2, '0')}:
              {String(durationSec % 60).padStart(2, '0')}
            </p>
            <p className="mt-3 text-xs text-muted-foreground">上滑取消 · 右滑转文字</p>
          </div>
        </div>
      )}

      {/* 1. 上方紧凑 [Chat | Task] 模式切换胶囊（对齐 Gemini 移动版图 1） */}
      <div className="mb-2 flex justify-center">
        <div className="inline-flex items-center rounded-full border border-border/70 bg-surface-1 p-0.5 shadow-xs">
          <button
            type="button"
            onClick={() => onChangeSceneMode('chat')}
            className={`min-h-[28px] rounded-full px-3.5 text-xs font-medium transition-all ${
              sceneMode === 'chat'
                ? 'bg-background text-foreground shadow-xs'
                : 'text-muted-foreground hover:text-foreground'
            }`}
          >
            Chat
          </button>
          <button
            type="button"
            onClick={() => onChangeSceneMode('agent')}
            className={`min-h-[28px] rounded-full px-3.5 text-xs font-medium transition-all ${
              sceneMode === 'agent'
                ? 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-300 shadow-xs'
                : 'text-muted-foreground hover:text-foreground'
            }`}
          >
            Task
          </button>
        </div>
      </div>

      {/* 2. 底部大圆角悬浮胶囊输入栏 */}
      <div className="flex items-end gap-1.5 rounded-[28px] border border-border/80 bg-surface-1 px-2 py-1.5 shadow-sm focus-within:border-emerald-500/60 transition-colors">
        {/* 左侧 + 号挂载附件/拍照 */}
        {onOpenSnap && (
          <button
            type="button"
            onClick={onOpenSnap}
            aria-label="添加附件或拍照"
            className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full text-foreground transition-transform active:scale-95 active:bg-surface-2"
          >
            <Plus className="h-5 w-5" strokeWidth={1.85} />
          </button>
        )}

        {/* 中间自适应输入框（16px 防止 iOS 自动放大） */}
        <textarea
          rows={1}
          value={textValue}
          onChange={(e) => setTextValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              handleSendText();
            }
          }}
          placeholder={
            isTranscribing
              ? '正在识别语音...'
              : sceneMode === 'agent'
              ? '说明任务或咨询规程...'
              : '接着提问...'
          }
          className="max-h-28 min-h-[44px] flex-1 resize-none bg-transparent px-1.5 py-2.5 text-[16px] leading-snug text-foreground placeholder:text-muted-foreground focus:outline-none"
        />

        {/* 右侧：有文字 -> 翡翠绿发送；空输入 -> 麦克风 + 声波全屏语音直达 */}
        {hasText ? (
          <button
            type="button"
            disabled={disabled}
            onClick={handleSendText}
            aria-label="发送消息"
            className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-emerald-600 text-white shadow-sm transition-transform active:scale-95 hover:bg-emerald-500 disabled:opacity-40"
          >
            <Send className="h-4 w-4" strokeWidth={2} />
          </button>
        ) : (
          <div className="flex shrink-0 items-center gap-1">
            <button
              type="button"
              disabled={disabled || isTranscribing}
              onPointerDown={handlePointerDown}
              onPointerMove={handlePointerMove}
              onPointerUp={finalizeRecording}
              onPointerCancel={finalizeRecording}
              aria-label="按住语音输入"
              className="flex h-11 w-11 items-center justify-center rounded-full text-foreground transition-transform touch-none active:scale-95 active:bg-emerald-500/15 active:text-emerald-500 disabled:opacity-40"
            >
              <Mic className="h-5 w-5" strokeWidth={1.85} />
            </button>

            <button
              type="button"
              disabled={disabled}
              onClick={() => setVoiceOverlayOpen(true)}
              aria-label="全屏语音对话"
              className="flex h-11 w-11 items-center justify-center rounded-full bg-emerald-500/12 text-emerald-600 dark:text-emerald-400 transition-transform active:scale-95 hover:bg-emerald-500/20 disabled:opacity-40"
            >
              <AudioLines className="h-5 w-5" strokeWidth={1.85} />
            </button>
          </div>
        )}
      </div>

      {/* 3. 沉浸式全屏语音模式（对齐图 3） */}
      {voiceOverlayOpen && (
        <div
          className="fixed inset-0 z-50 flex flex-col bg-background text-foreground"
          style={{
            paddingTop: 'env(safe-area-inset-top)',
            paddingBottom: 'env(safe-area-inset-bottom)',
          }}
        >
          {/* 顶栏：左侧连接状态 + 右侧字幕开关 */}
          <div className="flex h-14 items-center justify-between px-4">
            <div className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
              <span className="h-2 w-2 rounded-full bg-emerald-500" />
              <span>{sceneMode === 'agent' ? 'Task 语音模式' : 'Chat 语音模式'}</span>
            </div>

            <button
              type="button"
              onClick={() => setShowSubtitles((v) => !v)}
              aria-label={showSubtitles ? '隐藏实时字幕' : '显示实时字幕'}
              className="flex h-11 w-11 items-center justify-center rounded-full text-foreground transition-colors active:bg-surface-1"
            >
              {showSubtitles ? (
                <Captions className="h-5 w-5 text-emerald-500" strokeWidth={1.75} />
              ) : (
                <CaptionsOff className="h-5 w-5 text-muted-foreground" strokeWidth={1.75} />
              )}
            </button>
          </div>

          {/* 中部大留白与可选实时字幕 */}
          <div className="flex flex-1 flex-col items-center justify-center px-6 text-center">
            {showSubtitles ? (
              <div className="max-w-sm space-y-4">
                {lastTranscript && (
                  <p className="text-sm text-muted-foreground">“{lastTranscript}”</p>
                )}
                {latestAssistantReply ? (
                  <p className="line-clamp-6 text-base font-medium leading-relaxed text-foreground">
                    {latestAssistantReply}
                  </p>
                ) : (
                  <p className="text-base text-muted-foreground">
                    {gesture === 'recording'
                      ? `正在聆听 (${durationSec}s)...`
                      : isTranscribing
                      ? '正在识别并思考...'
                      : '按住下方光晕按钮开始语音对话'}
                  </p>
                )}
              </div>
            ) : (
              <p className="text-xs text-muted-foreground/70">字幕已隐藏，可点击右上角恢复</p>
            )}
          </div>

          {/* 底部控制栏：+ 传图 | 呼吸光晕主按钮 | 静音 | X 退出 */}
          <div className="flex items-center justify-center gap-5 px-4 pb-8 pt-4">
            {onOpenSnap ? (
              <button
                type="button"
                onClick={() => {
                  setVoiceOverlayOpen(false);
                  onOpenSnap();
                }}
                aria-label="拍照或传图"
                className="flex h-12 w-12 items-center justify-center rounded-full border border-border/70 bg-surface-1 text-foreground active:scale-95"
              >
                <Plus className="h-5 w-5" strokeWidth={1.75} />
              </button>
            ) : (
              <div className="h-12 w-12" />
            )}

            <button
              type="button"
              disabled={disabled || isTranscribing}
              onPointerDown={handlePointerDown}
              onPointerMove={handlePointerMove}
              onPointerUp={finalizeRecording}
              onPointerCancel={finalizeRecording}
              aria-label="按住说话"
              className={`flex h-16 w-24 items-center justify-center rounded-full border border-emerald-500/40 bg-gradient-to-b from-emerald-400/25 to-emerald-600/40 text-emerald-500 touch-none transition-transform active:scale-95 ${
                gesture === 'recording' ? 'gemini-voice-orb scale-105' : 'shadow-lg'
              }`}
            >
              <Mic className="h-6 w-6 text-emerald-500 dark:text-emerald-300" strokeWidth={2} />
            </button>

            <button
              type="button"
              onClick={() => {
                setVoiceMuted((m) => !m);
                if (typeof window !== 'undefined' && 'speechSynthesis' in window) {
                  window.speechSynthesis.cancel();
                }
              }}
              aria-label={voiceMuted ? '取消静音' : '静音'}
              className="flex h-12 w-12 items-center justify-center rounded-full border border-border/70 bg-surface-1 text-foreground active:scale-95"
            >
              {voiceMuted ? (
                <VolumeX className="h-5 w-5 text-rose-500" strokeWidth={1.75} />
              ) : (
                <Volume2 className="h-5 w-5" strokeWidth={1.75} />
              )}
            </button>

            <button
              type="button"
              onClick={() => setVoiceOverlayOpen(false)}
              aria-label="退出全屏语音"
              className="flex h-12 w-12 items-center justify-center rounded-full border border-border/70 bg-surface-1 text-foreground active:scale-95"
            >
              <X className="h-5 w-5" strokeWidth={1.75} />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
