import { t, useLocale } from '../../i18n'
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Button, Flex, Text, Textarea, Title } from '@mantine/core'
import { ragApi, ragError, type RagAnswer } from '../../lib/rag'
import { Disclosure } from '../ui'

export function MeetingChat({ meetingId }: { meetingId: string }) {
  useLocale()
  const cache = useQueryClient()
  const [question, setQuestion] = useState('')
  const [turns, setTurns] = useState<Array<{ question: string; result: RagAnswer }>>([])
  const configuration = useQuery({ queryKey: ['rag', 'config'], queryFn: ({ signal }) => ragApi.config(signal), staleTime: 0 })
  const statusKey = ['rag', meetingId, 'index']
  const status = useQuery({
    queryKey: statusKey, queryFn: ({ signal }) => ragApi.status(meetingId, signal),
    refetchInterval: (query) => ['queued', 'running'].includes(query.state.data?.status ?? '') ? 2000 : false,
  })
  const index = useMutation({
    mutationFn: () => ragApi.index(meetingId),
    onSuccess: (data) => { cache.setQueryData(statusKey, data) },
  })
  const ask = useMutation({
    mutationFn: (value: string) => ragApi.ask(meetingId, value),
    onSuccess: (result, value) => {
      setTurns((old) => [...old, { question: value, result }])
      setQuestion('')
    },
  })
  const config = configuration.data
  const cloud = config && (config.llm_provider === 'openai' || config.embedding_provider === 'openai')
  const busy = index.isPending || ['queued', 'running'].includes(status.data?.status ?? '')
  const ready = !status.isError && status.data?.status === 'ready'
  return <section className="transcript-panel rag-chat" aria-labelledby="meeting-chat-title">
    <Title order={2} size="h3" id="meeting-chat-title">{t("Вопросы к встрече")}</Title>
    <Text component="p" c="dimmed">{t("Задайте вопрос о решениях, участниках или договорённостях. К ответу будут приложены цитаты из стенограммы. Каждый вопрос рассматривается отдельно.")}</Text>
    {config && <Text component="p" size="sm" c="dimmed">{cloud
      ? t("В этом режиме вопросы и фрагменты встречи передаются в OpenAI. При подготовке поиска может передаваться вся стенограмма.")
      : t("Для ответов и поиска используются модели, настроенные на локальном сервере.")}</Text>}
    {(configuration.isError || status.isError) && <Alert color="red" role="alert">{ragError(configuration.error ?? status.error)}</Alert>}
    {config && !ready && !status.isError && <Flex direction="column" gap="md" align="start">
      {status.data?.status === 'failed' && <Text component="p" c="red">{t("Не удалось подготовить встречу. Проверьте подключение к модели и повторите попытку.")}</Text>}
      <Button size="md" onClick={() => index.mutate()} disabled={busy || !status.data || configuration.isError} loading={busy}>
        {busy ? t("Подготавливаем встречу…") : status.data?.status === 'failed' ? t("Повторить подготовку") : t("Подготовить встречу для вопросов")}
      </Button>
    </Flex>}
    {index.isError && <Alert color="red" role="alert">{ragError(index.error)}</Alert>}
    <div aria-live="polite" aria-relevant="additions" className="rag-turns">
      {turns.map((turn, number) => <article key={number} className="rag-turn">
        <Title order={3} size="h5">{turn.question}</Title>
        {turn.result.status === 'insufficient_evidence'
          ? <Text component="p">{t("В найденных фрагментах недостаточно данных для ответа. Уточните вопрос или проверьте стенограмму.")}</Text>
          : turn.result.claims.map((claim, i) => <div key={i}>
            <Text component="p">{claim.text}</Text>
            {claim.citations.map((citation, j) => <Disclosure key={j} className="rag-source" label={t("Цитата из стенограммы {0}.{1}", { "0": i + 1, "1": j + 1 })}>
              <blockquote>{citation.quote}</blockquote>
            </Disclosure>)}
          </div>)}
      </article>)}
    </div>
    <form onSubmit={(event) => { event.preventDefault(); if (ready && question.trim() && !ask.isPending) ask.mutate(question.trim()) }}>
      <Textarea label={t("Ваш вопрос")} id="meeting-question" size="md" value={question} onChange={(event) => setQuestion(event.target.value)}
        placeholder={t("Например: какой бюджет согласовали?")} maxLength={2000} disabled={!ready || ask.isPending} rows={3} />
      <Button type="submit" size="md" disabled={!ready || !question.trim() || !config || configuration.isError} loading={ask.isPending}>
        {ask.isPending ? t("Ищем ответ…") : t("Задать вопрос")}
      </Button>
    </form>
    {ask.isError && <Alert color="red" role="alert">{ragError(ask.error)}</Alert>}
  </section>
}
