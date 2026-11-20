'use client';

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Camera, Keyboard, Mic, Send, X } from 'lucide-react';
import { apiFetch } from '@/utils/api';

interface VoiceFirstInputBarProps {
  expertId?: string;
  kbId?: string;
  disabled?: boolean;
  onSubmit: (text: string) => void;
  onOpenSnap?: () => void;
}

type DragGesture = 'idle' | 'recording' | 'cancel' | 'edit';

/**
 * 移动端语音优先按住说话输入条
 * 胶囊式单手触控布局（44px 最低触控高度），支持上滑取消、右滑转文字及自动降级
 */
export function VoiceFirstInputBar({
  expertId,
  kbId,
  disabled = false,
  onSubmit,
  onOpenSnap,
}: VoiceFirstInputBarProps) {
  const [inputMode, setInputMode] = useState<'voice' | 'text'>('voice');
  const [textValue, setTextValue] = useState('');
  const [gesture, setGesture] = useState<DragGesture>('idle');
  const [isTranscribing, setIsTranscribing] = useState(false);
  const [toastMsg, setToastMsg] = useState<string | null>(null);
  const [durationSec, setDurationSec] = useState(0);

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
        showToast('本地语音引擎未配置，已切换为键盘输入');
        setInputMode('text');
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
          if (editAfterRecognize) {
            setTextValue(transcript);
            setInputMode('text');
          } else {
            onSubmit(transcript);
          }
        };
        rec.onerror = () => {
          showToast('浏览器语音识别不可用，请改用文字输入');
          setInputMode('text');
        };
        rec.start();
      } catch {
        setInputMode('text');
      }
    },
    [onSubmit, showToast]
  );

  const uploadAndTranscribe = useCallback(
    async (blob: Blob, editAfterRecognize: boolean) => {
      setIsTranscribing(true);
      try {
        const formData = new FormData();
        const ext = blob.type.includes('mp4') ? 'm4a' : 'webm';
        formData.append('file', blob, `voice.${ext}`);
        if (expertId) formData.append('expert_id', expertId);
        if (kbId) formData.append('kb_id', kbId);

        const res = await apiFetch('/api/v1/audio/transcriptions', {
          method: 'POST',
          body: formData,
        });

        if (res.status === 501) {
          const errPayload = await res.json().catch(() => ({}));
          if (errPayload?.fallback === 'web_speech_api') {
            tryBrowserSpeechFallback(editAfterRecognize);
            return;
          }
        }

        if (!res.ok) {
          showToast('语音转写失败，请改用文字输入');
          setInputMode('text');
          return;
        }

        const data = await res.json();
        const text = String(data?.text || '').trim();
        if (!text) {
          showToast('未识别到清晰语音，请重试');
          return;
        }

        if (editAfterRecognize) {
          setTextValue(text);
          setInputMode('text');
        } else {
          onSubmit(text);
        }
      } catch {
        showToast('网络异常，语音上传失败');
      } finally {
        setIsTranscribing(false);
      }
    },
    [expertId, kbId, onSubmit, showToast, tryBrowserSpeechFallback]
  );

  const handlePointerDown = async (e: React.PointerEvent<HTMLButtonElement>) => {
    if (disabled || isTranscribing) return;
    e.preventDefault();

    if (typeof window !== 'undefined' && !window.isSecureContext) {
      showToast('当前局域网为 HTTP 环境，浏览器限制麦克风调用，已切换文字输入');
      setInputMode('text');
      return;
    }

    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
      showToast('当前浏览器不支持录音，已切换文字输入');
      setInputMode('text');
      return;
    }

    try {
      e.currentTarget.setPointerCapture(e.pointerId);
    } catch {
      // 忽略部分移动端浏览器的捕获异常
    }

    startPosRef.current = { x: e.clientX, y: e.clientY };
    startTimeRef.current = Date.now();
    gestureRef.current = 'recording';
    setGesture('recording');
    setDurationSec(0);

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      audioChunksRef.current = [];

      const mimeType = MediaRecorder.isTypeSupported('audio/webm')
        ? 'audio/webm'
        : MediaRecorder.isTypeSupported('audio/mp4')
        ? 'audio/mp4'
        : '';
      const recorder = mimeType
        ? new MediaRecorder(stream, { mimeType })
        : new MediaRecorder(stream);

      mediaRecorderRef.current = recorder;
      recorder.ondataavailable = (ev) => {
        if (ev.data && ev.data.size > 0) {
          audioChunksRef.current.push(ev.data);
        }
      };
      recorder.start();

      timerRef.current = window.setInterval(() => {
        setDurationSec((sec) => sec + 1);
      }, 1000);
    } catch {
      stopMediaTracks();
      gestureRef.current = 'idle';
      setGesture('idle');
      showToast('无法获取麦克风权限，请改用键盘输入');
      setInputMode('text');
    }
  };

  const handlePointerMove = (e: React.PointerEvent<HTMLButtonElement>) => {
    if (gestureRef.current === 'idle' || !startPosRef.current) return;
    const dx = e.clientX - startPosRef.current.x;
    const dy = startPosRef.current.y - e.clientY;

    let next: DragGesture = 'recording';
    if (dy > 60) {
      next = 'cancel';
    } else if (dx > 60) {
      next = 'edit';
    }
    if (next !== gestureRef.current) {
      gestureRef.current = next;
      setGesture(next);
    }
  };

  const finalizeRecording = () => {
    const finalGesture = gestureRef.current;
    const elapsedMs = Date.now() - startTimeRef.current;
    gestureRef.current = 'idle';
    setGesture('idle');
    startPosRef.current = null;

    const recorder = mediaRecorderRef.current;
    mediaRecorderRef.current = null;

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
      if (elapsedMs < 450) {
        showToast('说话时间太短，请按住说话');
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

  return (
    <div className="relative border-t border-border/60 bg-background/95 px-3 py-2.5 backdrop-blur-md">
      {toastMsg && (
        <div className="pointer-events-none absolute -top-12 left-1/2 z-30 -translate-x-1/2 whitespace-nowrap rounded-full border border-border bg-surface-1 px-4 py-1.5 text-xs font-medium text-foreground shadow-md">
          {toastMsg}
        </div>
      )}

      {gesture !== 'idle' && (
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
                ? '松开手指，转为文字微调'
                : '正在聆听，松开即发送'}
            </p>
            <p className="mt-1.5 font-mono text-sm tabular-nums text-muted-foreground">
              {String(Math.floor(durationSec / 60)).padStart(2, '0')}:
              {String(durationSec % 60).padStart(2, '0')}
            </p>
            <p className="mt-3 text-xs text-muted-foreground">
              上滑取消 · 右滑转文字编辑
            </p>
          </div>
        </div>
      )}

      {inputMode === 'voice' ? (
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setInputMode('text')}
            aria-label="切换文字输入"
            className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full border border-border/70 bg-surface-1 text-foreground transition-transform active:scale-95 active:bg-surface-2"
          >
            <Keyboard className="h-5 w-5" strokeWidth={1.75} />
          </button>

          <button
            type="button"
            disabled={disabled || isTranscribing}
            onPointerDown={handlePointerDown}
            onPointerMove={handlePointerMove}
            onPointerUp={finalizeRecording}
            onPointerCancel={finalizeRecording}
            className={`flex h-11 flex-1 select-none items-center justify-center gap-2 rounded-full border text-sm font-medium shadow-sm transition-all touch-none ${
              gesture === 'recording'
                ? 'border-emerald-500 bg-emerald-500/15 text-emerald-600 dark:text-emerald-400'
                : gesture === 'cancel'
                ? 'border-rose-500 bg-rose-500/15 text-rose-600 dark:text-rose-400'
                : 'border-border/70 bg-surface-1 text-foreground active:scale-[0.99] active:bg-surface-2'
            } disabled:opacity-50`}
          >
            <Mic className="h-4 w-4" strokeWidth={1.75} />
            <span>
              {isTranscribing
                ? '正在识别语音...'
                : gesture !== 'idle'
                ? '松开结束'
                : '按住说话'}
            </span>
          </button>

          {onOpenSnap && (
            <button
              type="button"
              onClick={onOpenSnap}
              aria-label="随手拍识别"
              className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full border border-border/70 bg-surface-1 text-foreground transition-transform active:scale-95 active:bg-surface-2"
            >
              <Camera className="h-5 w-5" strokeWidth={1.75} />
            </button>
          )}
        </div>
      ) : (
        <div className="flex items-end gap-2">
          <button
            type="button"
            onClick={() => setInputMode('voice')}
            aria-label="切换语音输入"
            className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full border border-border/70 bg-surface-1 text-foreground transition-transform active:scale-95 active:bg-surface-2"
          >
            <Mic className="h-5 w-5" strokeWidth={1.75} />
          </button>

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
            placeholder="输入问题，点击发送..."
            className="max-h-28 min-h-[44px] flex-1 resize-none rounded-2xl border border-border/70 bg-surface-1 px-4 py-2.5 text-[15px] leading-snug text-foreground placeholder:text-muted-foreground focus:border-emerald-500 focus:outline-none"
          />

          {onOpenSnap && !textValue.trim() ? (
            <button
              type="button"
              onClick={onOpenSnap}
              aria-label="随手拍识别"
              className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full border border-border/70 bg-surface-1 text-foreground transition-transform active:scale-95 active:bg-surface-2"
            >
              <Camera className="h-5 w-5" strokeWidth={1.75} />
            </button>
          ) : (
            <button
              type="button"
              disabled={!textValue.trim() || disabled}
              onClick={handleSendText}
              aria-label="发送"
              className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-emerald-600 text-white shadow-sm transition-transform active:scale-95 hover:bg-emerald-500 disabled:opacity-40"
            >
              <Send className="h-4 w-4" strokeWidth={1.75} />
            </button>
          )}
        </div>
      )}
    </div>
  );
}
