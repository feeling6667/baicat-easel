import { useState, useRef, useEffect } from 'react';
import MessageBubble from './MessageBubble';
import QuestionCards from './QuestionCards';
import BrushEntry from './BrushEntry';
import ModelPicker from './ModelPicker';
import type { ChatSession, ChatMessage, StreamState, ThinkStep } from '../lib/store';
import { uploadFiles, adoptOversize } from '../lib/api';
import type { UploadedFile } from '../lib/api';
import { IconArrowUp, IconStop, IconPlus, IconFile } from './icons';

interface ChatPageProps {
  apiDirect: boolean;
  session: ChatSession;
  stream?: StreamState;          // 进行中的流式态（来自 App，切页也不丢）
  onSend: (displayText: string, attachments?: UploadedFile[]) => void;
  onStop: () => void;
  onResend: (
    userIndex: number,
    displayText: string,
    attachments?: UploadedFile[],
    legacyAgentText?: string,
  ) => void; // 重试：仅对最后一轮
  onQuestionAnswered?: (questionId: string) => void;   // 某道问答题提交成功（App 记录答过，重放不再出现）
  // ---- 模型下拉（本机补丁）：值由 App 持有，选中即写后端 .env，重启不丢 ----
  model?: string;
  onModelChange?: (modelId: string) => void;
}

/**
 * 流式「思考进度」所需的附加字段（由 App/store 侧提供）。
 * 用交叉类型 + 可选字段读取：字段尚未落到 StreamState 时也不会编译失败或白屏。
 */
type StreamProgressFields = {
  startedAt?: number;
  thinkingTotal?: number;
  lastChunkAt?: number;
  progress?: string;       // 本轮累计的实时输出文本（SSE progress 事件差分增量拼接）
  steps?: ThinkStep[];     // 工具调用流水（SSE steps 事件）：进行到第几步
};
type StreamStateWithProgress = StreamState & StreamProgressFields;

// 空态推荐（贴合 Easel 社媒创作场景）
const SUGGESTIONS = [
  { icon: '🔥', title: '蹭个热点', prompt: '看看现在微博和抖音有什么热搜，挑几个适合我做二创的选题' },
  { icon: '✍️', title: '写小红书文案', prompt: '帮我写一条小红书种草文案，主题先问我' },
  { icon: '🎴', title: '做金句卡片', prompt: '把一句走心的话做成一张适合发朋友圈的金句卡片' },
  { icon: '🎬', title: '口播脚本', prompt: '帮我写一条 60 秒的口播短视频脚本，主题先问我' },
];

function greeting(): string {
  const h = new Date().getHours();
  const g = h < 6 ? '夜深了' : h < 12 ? '上午好' : h < 14 ? '中午好' : h < 18 ? '下午好' : '晚上好';
  return `${g}，想创作点什么？`;
}

const ATTACHMENT_MARKER = '【附件素材】';

/**
 * 编辑重发时交给 Agent 的 legacyAgentText。
 * 硬性要求：Agent 必须收到**新文本**，所以绝不把旧的 agentContent 原样回传（那等于让 Agent
 * 收到编辑前的旧话）。这里只做一件事：把仅存在于旧 agentContent 里的附件素材块拼到新文本后面，
 * 让老格式消息的素材路径不丢。
 * - 有结构化 attachments 的新格式消息：路径由 attachments 参数直达 Agent，返回 undefined（不回传旧文本）；
 * - 老格式消息（附件块被存进 agentContent）：新文本 + 【附件素材】块，附件路径保留、旧正文不覆盖新文本。
 */
function legacyAgentTextFor(msg: ChatMessage, newText: string): string | undefined {
  if (msg.attachments?.length) return undefined;
  const src = msg.agentContent;
  if (!src) return undefined;
  const at = src.indexOf(ATTACHMENT_MARKER);
  if (at < 0) return undefined;   // 不含附件块：一律不回传，防止旧文本泄漏给 Agent
  const block = src.slice(at).trim();
  return block ? `${newText}\n\n${block}` : undefined;
}

export default function ChatPage({ apiDirect, session, stream, onSend, onStop, onResend, onQuestionAnswered, model, onModelChange }: ChatPageProps) {
  const [input, setInput] = useState('');
  const [attachments, setAttachments] = useState<UploadedFile[]>([]);
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [maxMb, setMaxMb] = useState(50);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const isComposingRef = useRef(false);

  const isStreaming = !!stream;
  // 思考进度附加字段（startedAt / thinkingTotal / lastChunkAt）由 App/store 侧提供；
  // 这里按可选字段读取，字段缺失或类型尚未就绪都不影响构建与渲染。
  const liveStream = stream as StreamStateWithProgress | undefined;
  const isEmpty = session.messages.length === 0 && !isStreaming;

  useEffect(() => {
    fetch('/api/upload/limits').then((r) => r.json())
      .then((d) => { if (d?.max_mb) setMaxMb(d.max_mb); }).catch(() => {});
  }, []);

  const doUpload = async (fs: FileList | File[]) => {
    const arr = Array.from(fs);
    if (!arr.length) return;
    const cap = maxMb * 1024 * 1024;
    const big = arr.filter((f) => f.size > cap);
    const small = arr.filter((f) => f.size <= cap);

    if (big.length) {
      // 超限：不走上传通道，复制进收件箱后作为普通附件（界面零新增元素）
      setUploading(true);
      try {
        const saved = await adoptOversize(big, session.id);
        setAttachments((a) => [...a, ...saved]);
      } catch (err) {
        alert((err as Error).message || '超限文件处理失败');
      } finally {
        setUploading(false);
      }
    }
    if (!small.length) return;
    setUploading(true);
    try {
      const saved = await uploadFiles(small, session.id);
      setAttachments((a) => [...a, ...saved]);
    } catch (err) {
      alert((err as Error).message || '上传失败');
    } finally {
      setUploading(false);
    }
  };
  const onDrop = (e: React.DragEvent) => {
    e.preventDefault(); setDragOver(false);
    if (e.dataTransfer.files?.length) doUpload(e.dataTransfer.files);
  };
  const onPaste = (e: React.ClipboardEvent) => {
    if (e.clipboardData.files?.length) { e.preventDefault(); doUpload(e.clipboardData.files); }
  };
  const removeAttachment = (path: string) => setAttachments((a) => a.filter((x) => x.path !== path));

  useEffect(() => {
    if (!isEmpty) messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [session.messages, stream?.content, stream?.thinking, stream?.activity, stream?.stillWorking, isEmpty]);

  useEffect(() => {
    const el = textareaRef.current;
    if (el) {
      el.style.height = 'auto';
      el.style.height = Math.min(el.scrollHeight, 180) + 'px';
    }
  }, [input]);

  const handleSend = () => {
    const trimmed = input.trim();
    if ((!trimmed && attachments.length === 0) || isStreaming || uploading) return;
    // 附件通过结构化字段发送；用户消息气泡只显示用户实际输入的文字。
    onSend(trimmed, attachments);
    setInput('');
    setAttachments([]);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // 输入法确认候选词/英文的回车交给输入法处理，不能触发发送。
    // 部分浏览器先触发 compositionend，此时仍需用 229 识别输入法按键。
    if (isComposingRef.current || e.nativeEvent.isComposing || e.nativeEvent.keyCode === 229) return;
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  // 编辑并重发（任意一轮用户消息都可用）：截断到该条、用新文本重新发起流。
  // 该条之后还有消息时先确认——重发会丢弃它们。返回 false 表示未受理（流式中 / 用户取消确认）。
  const handleEditSave = (index: number, msg: ChatMessage, text: string): boolean => {
    if (isStreaming) return false;
    const dropped = session.messages.length - index - 1;
    if (dropped > 0 && !window.confirm(
      `这条消息之后还有 ${dropped} 条对话记录，保存后会连同它们一起丢弃，并从这条重新生成。确定继续？`,
    )) return false;
    onResend(index, text, msg.attachments, legacyAgentTextFor(msg, text));
    return true;
  };

  const inputBox = (hero: boolean) => (
    <div className={`composer ${hero ? 'composer-hero' : ''} ${dragOver ? 'composer-drag' : ''}`}
      onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
      onDragLeave={(e) => { e.preventDefault(); setDragOver(false); }}
      onDrop={onDrop}>
      {attachments.length > 0 && (
        <div className="composer-attachments">
          {attachments.map((a) => (
            <span key={a.path} className="attach-chip" title={a.path}>
              <IconFile size={12} /> <span className="attach-name">{a.name}</span>
              <button className="attach-x" onClick={() => removeAttachment(a.path)} title="移除">×</button>
            </span>
          ))}
        </div>
      )}
      <div className="composer-top">
        {apiDirect ? <span className="composer-hint">API 直连 · 文字与附件分析</span>
          : <BrushEntry onPick={(t) => { setInput(t); requestAnimationFrame(() => textareaRef.current?.focus()); }} />}
        <textarea
          ref={textareaRef}
          className="chat-input"
          placeholder={dragOver ? '松手上传素材…' : hero ? '把你的想法告诉我，选题 / 文案 / 卡片 / 视频 / 发布都行…（可拖入图片/文档当素材）' : '发消息…（Enter 发送，Shift+Enter 换行，可拖入/粘贴素材）'}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onCompositionStart={() => { isComposingRef.current = true; }}
          onCompositionEnd={() => { isComposingRef.current = false; }}
          onBlur={() => { isComposingRef.current = false; }}
          onKeyDown={handleKeyDown}
          onPaste={onPaste}
          rows={1}
          autoFocus={hero}
        />
      </div>
      <input ref={fileInputRef} type="file" multiple hidden
        onChange={(e) => { if (e.target.files) doUpload(e.target.files); e.target.value = ''; }} />
      <div className="composer-bar">
        <div className="composer-bar-left">
          <button className="composer-attach-btn" onClick={() => fileInputRef.current?.click()}
            disabled={isStreaming || uploading} title={`添加素材（图片/文档）；超过 ${maxMb}MB 的大文件将自动存为本地素材（不走上传）`}>
            <IconPlus size={15} /> {uploading ? '上传中…' : '素材'}
          </button>
          {onModelChange && (
            <ModelPicker value={model || ''} onChange={onModelChange} disabled={isStreaming || uploading} />
          )}
        </div>
        <div className="composer-bar-right">
          <span className="composer-hint">{isStreaming ? '生成中…' : 'Enter 发送 · Shift+Enter 换行'}</span>
          {isStreaming ? (
            <button className="send-btn" onClick={onStop} title="停止生成"><IconStop size={15} /></button>
          ) : (
            <button className="send-btn" onClick={handleSend} disabled={(!input.trim() && !attachments.length) || uploading} title="发送"><IconArrowUp size={17} /></button>
          )}
        </div>
      </div>
    </div>
  );

  // ---- 空态：居中欢迎页 ----
  if (isEmpty) {
    return (
      <div className="chat-page">
        <div className="chat-hero">
          <div className="chat-hero-brand">
            <img src="./static/easel-icon-transparent.png" alt="" />
            <span>Easel</span>
          </div>
          <h1 className="chat-hero-title">{greeting()}</h1>
          <p className="chat-hero-sub">{apiDirect ? 'API 直连：写文案、策划选题、分析附件。本机工具和自动发布未启用。'
            : '从选题到发布，一站式帮你把想法做成能发的内容。'}</p>
          {inputBox(true)}
          <div className="suggestions">
            {SUGGESTIONS.filter((s) => !apiDirect || ['写小红书文案', '口播脚本'].includes(s.title)).map((s) => (
              <button key={s.title} className="card card-hover suggestion-card"
                onClick={() => { if (!isStreaming) onSend(s.prompt); }}>
                <span className="suggestion-icon">{s.icon}</span>
                <span className="suggestion-body">
                  <span className="suggestion-title">{s.title}</span>
                  <span className="suggestion-text">{s.prompt}</span>
                </span>
              </button>
            ))}
          </div>
        </div>
      </div>
    );
  }

  // ---- 对话态 ----
  const displayMessages: ChatMessage[] = [...session.messages];
  if (isStreaming) displayMessages.push({ role: 'assistant', content: stream!.content || '' });

  return (
    <div className="chat-page">
      <div className="chat-messages">
        <div className="chat-thread">
          {displayMessages.map((msg, i) => {
            const isLast = i === displayMessages.length - 1;
            const live = isStreaming && isLast && msg.role === 'assistant';
            const isFinal = !live && i < session.messages.length;
            let actions;
            if (isFinal) {
              const copy = () => navigator.clipboard?.writeText(msg.content);
              // 只允许对「最后一轮」重试，契合 OpenClaw append-only 模型（不改写历史）；
              // 编辑并重发不受此限：任意一轮用户消息都可编辑，确认后截断该条之后重发。
              const isLastFinal = i === session.messages.length - 1;
              if (msg.role === 'user') {
                actions = {
                  onCopy: copy,
                  onRetry: isLastFinal
                    ? () => onResend(i, msg.content, msg.attachments, msg.agentContent)
                    : undefined,
                  onEditSave: (text: string) => handleEditSave(i, msg, text),
                  canModify: !isStreaming,
                };
              } else {
                const pi = i - 1;
                const prevUser = pi >= 0 && session.messages[pi]?.role === 'user' ? session.messages[pi] : null;
                actions = {
                  onCopy: copy,
                  onRetry: (isLastFinal && prevUser)
                    ? () => onResend(pi, prevUser.content, prevUser.attachments, prevUser.agentContent)
                    : undefined,
                  canModify: !isStreaming,
                };
              }
            }
            return (
              <MessageBubble
                key={`${i}-${msg.role}`}
                message={msg}
                isStreaming={live}
                thinking={live ? stream!.thinking : ''}
                progress={live ? liveStream?.progress : undefined}
                steps={live ? liveStream?.steps : undefined}
                activity={live ? stream!.activity : ''}
                stillWorking={live ? stream!.stillWorking : ''}
                startedAt={live ? liveStream?.startedAt : undefined}
                thinkingTotal={live ? liveStream?.thinkingTotal : undefined}
                lastChunkAt={live ? liveStream?.lastChunkAt : undefined}
                actions={actions}
              />
            );
          })}
          {isStreaming && (stream!.questions?.length ?? 0) > 0 && (
            <QuestionCards questions={stream!.questions || []} onDone={(qid) => onQuestionAnswered?.(qid)} />
          )}
          <div ref={messagesEndRef} />
        </div>
      </div>

      <div className="chat-input-area">
        <div className="chat-input-inner">{inputBox(false)}</div>
      </div>
    </div>
  );
}
