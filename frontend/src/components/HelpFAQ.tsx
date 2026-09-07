import { useState } from 'react'
import { BookOpen, ChevronDown, HelpCircle, Network, ShieldCheck, X } from 'lucide-react'

type Faq = { question: string; answer: string }

const sections: Array<{ title: string; icon: typeof BookOpen; items: Faq[] }> = [
  {
    title: '快速开始',
    icon: BookOpen,
    items: [
      { question: '从哪里开始？', answer: '在首页选择常规、时序或多模态数据，再按五步向导完成选择、处理、映射和构建。' },
      { question: '五步分别做什么？', answer: '选择数据集 → 选择当前内容 → 查看处理和发送范围 → 确认本体映射 → 启动构建。' },
    ],
  },
  {
    title: '模型与隐私',
    icon: ShieldCheck,
    items: [
      { question: 'MiniMax M3 做什么？', answer: 'M3 负责主构建：语义分类、字段切分、类和关系建议。所有建议在写入前都可以编辑和确认。' },
      { question: '0.8B 做什么？', answer: '本地模型负责构建后的独立质量审查，也可用于私密数据和轻量整理。页面只展示可核验的审查步骤和证据。' },
      { question: '标准与私密有什么区别？', answer: '标准数据可以把确认范围发送给 M3；私密数据禁止云端调用，只走本地模型、规则或人工映射。' },
    ],
  },
  {
    title: '查看本体',
    icon: Network,
    items: [
      { question: '可以查看哪些结果？', answer: '构建后可查看本体关系、实体记录、逻辑规则和质量审查；时序数据还可查看时间轴，多模态数据可查看证据工作区和点云。' },
      { question: '证据从哪里来？', answer: '每个可追溯断言都关联原始数据集、文件、样本或媒体资产。点击证据可以回到对应来源。' },
    ],
  },
  {
    title: '故障处理',
    icon: HelpCircle,
    items: [
      { question: '模型不可用怎么办？', answer: '标准任务会暂停并等待 M3 恢复；私密任务可继续使用规则和人工映射。两种状态都会在任务详情中明确标记。' },
      { question: '任务中途刷新会丢失吗？', answer: '不会。下载、构建和审查任务保存在服务端，刷新后可以继续查看、取消或重试。' },
    ],
  },
]

export default function HelpFAQ() {
  const [open, setOpen] = useState(false)
  const [expanded, setExpanded] = useState<string | null>(sections[0].items[0].question)

  return (
    <>
      <button
        type="button"
        aria-label="打开帮助"
        onClick={() => setOpen(true)}
        className="fixed bottom-24 right-6 z-40 flex h-11 w-11 items-center justify-center rounded-full border border-slate-300 bg-white text-slate-700 shadow-[0_8px_24px_rgba(15,23,42,.12)] transition hover:border-slate-500 hover:text-slate-950 focus:outline-none focus:ring-2 focus:ring-blue-200"
      >
        <HelpCircle size={20} strokeWidth={1.8} />
      </button>

      {open && (
        <div className="fixed inset-0 z-50" role="dialog" aria-modal="true" aria-label="帮助">
          <button aria-label="关闭帮助" className="absolute inset-0 bg-slate-950/20" onClick={() => setOpen(false)} />
          <aside className="absolute bottom-4 right-4 top-4 flex w-[min(430px,calc(100vw-32px))] flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-[0_18px_50px_rgba(15,23,42,.22)]">
            <div className="flex items-center justify-between border-b border-slate-200 px-5 py-4">
              <div>
                <p className="wb-eyebrow">帮助</p>
                <h2 className="mt-1 text-lg font-semibold tracking-tight">使用问答</h2>
              </div>
              <button type="button" onClick={() => setOpen(false)} className="rounded-lg p-2 text-slate-400 hover:bg-slate-100 hover:text-slate-800" aria-label="关闭帮助">
                <X size={18} />
              </button>
            </div>
            <div className="workbench-scrollbar flex-1 space-y-5 overflow-y-auto px-5 py-5">
              {sections.map(({ title, icon: Icon, items }) => (
                <section key={title}>
                  <div className="mb-2 flex items-center gap-2 text-xs font-semibold text-slate-500">
                    <Icon size={14} />{title}
                  </div>
                  <div className="divide-y divide-slate-100 rounded-xl border border-slate-200">
                    {items.map(item => {
                      const isExpanded = expanded === item.question
                      return (
                        <div key={item.question}>
                          <button
                            type="button"
                            onClick={() => setExpanded(isExpanded ? null : item.question)}
                            className="flex w-full items-center justify-between gap-3 px-3.5 py-3 text-left text-sm font-medium text-slate-800 hover:bg-slate-50"
                            aria-expanded={isExpanded}
                          >
                            <span>{item.question}</span>
                            <ChevronDown size={15} className={`shrink-0 text-slate-400 transition-transform ${isExpanded ? 'rotate-180' : ''}`} />
                          </button>
                          {isExpanded && <p className="px-3.5 pb-3.5 text-xs leading-5 text-slate-500">{item.answer}</p>}
                        </div>
                      )
                    })}
                  </div>
                </section>
              ))}
            </div>
            <div className="border-t border-slate-200 bg-slate-50 px-5 py-3 text-[11px] text-slate-500">
              页面中的数据、模型和任务状态以当前服务返回结果为准。
            </div>
          </aside>
        </div>
      )}
    </>
  )
}
