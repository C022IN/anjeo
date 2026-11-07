import type { ReactNode } from 'react'
import { ActivityIndicator, Pressable, StyleSheet, Text, View, type ViewStyle } from 'react-native'

export const color = {
  bg: '#0b0d12',
  card: '#141821',
  line: '#232937',
  text: '#e8ebf2',
  dim: '#8b93a7',
  accent: '#7c9cff',
  ok: '#3ecf8e',
  warn: '#f5b84a',
  bad: '#ff6b6b',
}

export function Card({ children, style }: { children: ReactNode; style?: ViewStyle }) {
  return <View style={[s.card, style]}>{children}</View>
}

export function Button({
  label,
  onPress,
  kind = 'primary',
  busy,
  disabled,
}: {
  label: string
  onPress: () => void
  kind?: 'primary' | 'ghost' | 'danger' | 'ok'
  busy?: boolean
  disabled?: boolean
}) {
  const bg = { primary: color.accent, ghost: 'transparent', danger: color.bad, ok: color.ok }[kind]
  return (
    <Pressable
      accessibilityRole="button"
      onPress={onPress}
      disabled={busy || disabled}
      style={({ pressed }) => [
        s.btn,
        { backgroundColor: bg, opacity: pressed || disabled ? 0.6 : 1 },
        kind === 'ghost' && { borderWidth: 1, borderColor: color.line },
      ]}
    >
      {busy ? <ActivityIndicator color={color.text} /> : <Text style={s.btnText}>{label}</Text>}
    </Pressable>
  )
}

export function Pill({ text, tone }: { text: string; tone: 'ok' | 'warn' | 'bad' | 'dim' }) {
  const c = { ok: color.ok, warn: color.warn, bad: color.bad, dim: color.dim }[tone]
  return (
    <View style={[s.pill, { borderColor: c }]}>
      <Text style={[s.pillText, { color: c }]}>{text}</Text>
    </View>
  )
}

export const policyTone = { allow: 'ok', approve: 'warn', deny: 'bad' } as const

export function Empty({ text }: { text: string }) {
  return <Text style={[t.dim, { textAlign: 'center', marginTop: 32 }]}>{text}</Text>
}

export function ErrorLine({ text }: { text: string | null }) {
  return text ? <Text style={[t.body, { color: color.bad, marginBottom: 8 }]}>{text}</Text> : null
}

export function ago(epochSeconds: number): string {
  const d = Math.max(0, Date.now() / 1000 - epochSeconds)
  if (d < 60) return 'just now'
  if (d < 3600) return `${Math.floor(d / 60)}m ago`
  if (d < 86400) return `${Math.floor(d / 3600)}h ago`
  return `${Math.floor(d / 86400)}d ago`
}

export function until(epochSeconds: number): string {
  const d = epochSeconds - Date.now() / 1000
  if (d <= 0) return 'expired'
  if (d < 3600) return `expires in ${Math.ceil(d / 60)}m`
  return `expires in ${Math.round(d / 3600)}h`
}

export const t = StyleSheet.create({
  h1: { color: color.text, fontSize: 26, fontWeight: '700', marginBottom: 12 },
  h2: { color: color.text, fontSize: 16, fontWeight: '600', marginBottom: 6 },
  body: { color: color.text, fontSize: 15, lineHeight: 21 },
  dim: { color: color.dim, fontSize: 13, lineHeight: 18 },
  mono: { color: color.text, fontFamily: 'Courier', fontSize: 12 },
})

export const s = StyleSheet.create({
  card: {
    backgroundColor: color.card,
    borderColor: color.line,
    borderWidth: 1,
    borderRadius: 14,
    padding: 14,
    marginBottom: 10,
  },
  btn: { borderRadius: 10, paddingVertical: 11, paddingHorizontal: 16, alignItems: 'center', minWidth: 90 },
  btnText: { color: color.text, fontWeight: '600', fontSize: 15 },
  pill: { borderWidth: 1, borderRadius: 999, paddingHorizontal: 8, paddingVertical: 2, alignSelf: 'flex-start' },
  pillText: { fontSize: 11, fontWeight: '600', textTransform: 'uppercase' },
  input: {
    backgroundColor: color.bg,
    borderColor: color.line,
    borderWidth: 1,
    borderRadius: 10,
    color: color.text,
    padding: 12,
    fontSize: 15,
    marginBottom: 10,
  },
  row: { flexDirection: 'row', alignItems: 'center', gap: 8 },
})
