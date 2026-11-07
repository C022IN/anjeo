import { StatusBar } from 'expo-status-bar'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { ActivityIndicator, Pressable, SafeAreaView, StyleSheet, Text, View } from 'react-native'
import { client, loadConnection, saveConnection, type Connection } from './src/api'
import { ActivityScreen, AskScreen, InboxScreen, MeScreen, ToolsScreen } from './src/screens'
import { color } from './src/ui'

type Tab = 'ask' | 'inbox' | 'tools' | 'activity' | 'me'
const TABS: { key: Tab; label: string }[] = [
  { key: 'ask', label: 'Ask' },
  { key: 'inbox', label: 'Inbox' },
  { key: 'tools', label: 'Tools' },
  { key: 'activity', label: 'Activity' },
  { key: 'me', label: 'Me' },
]

export default function App() {
  const [connection, setConnection] = useState<Connection | null>(null)
  const [tab, setTab] = useState<Tab>('ask')
  const [pending, setPending] = useState(0)

  useEffect(() => {
    loadConnection().then(setConnection)
  }, [])

  const api = useMemo(() => (connection ? client(connection) : null), [connection])

  const refreshBadge = useCallback(async () => {
    if (!api) return
    try {
      setPending((await api.approvals('pending')).length)
    } catch {
      // The badge is best-effort; screens surface connection errors themselves.
    }
  }, [api])

  useEffect(() => {
    refreshBadge()
    const id = setInterval(refreshBadge, 15_000)
    return () => clearInterval(id)
  }, [refreshBadge])

  async function updateConnection(c: Connection) {
    await saveConnection(c)
    setConnection(c)
  }

  if (!connection || !api) {
    return (
      <View style={[styles.root, { justifyContent: 'center' }]}>
        <ActivityIndicator color={color.accent} />
      </View>
    )
  }

  return (
    <SafeAreaView style={styles.root}>
      <StatusBar style="light" />
      <View style={{ flex: 1 }}>
        {tab === 'ask' && <AskScreen api={api} onQueued={refreshBadge} />}
        {tab === 'inbox' && <InboxScreen api={api} onChange={refreshBadge} />}
        {tab === 'tools' && <ToolsScreen api={api} onQueued={refreshBadge} />}
        {tab === 'activity' && <ActivityScreen api={api} />}
        {tab === 'me' && <MeScreen api={api} connection={connection} onConnection={updateConnection} />}
      </View>
      <View style={styles.tabs}>
        {TABS.map(({ key, label }) => (
          <Pressable key={key} onPress={() => setTab(key)} style={styles.tab} accessibilityRole="tab">
            <Text style={[styles.tabText, tab === key && { color: color.accent }]}>{label}</Text>
            {key === 'inbox' && pending > 0 && (
              <View style={styles.badge}>
                <Text style={styles.badgeText}>{pending}</Text>
              </View>
            )}
          </Pressable>
        ))}
      </View>
    </SafeAreaView>
  )
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: color.bg },
  tabs: { flexDirection: 'row', borderTopWidth: 1, borderColor: color.line, backgroundColor: color.card },
  tab: { flex: 1, alignItems: 'center', paddingVertical: 12 },
  tabText: { color: color.dim, fontWeight: '600', fontSize: 13 },
  badge: {
    position: 'absolute',
    top: 4,
    right: '22%',
    backgroundColor: color.warn,
    borderRadius: 9,
    minWidth: 18,
    paddingHorizontal: 4,
    alignItems: 'center',
  },
  badgeText: { color: color.bg, fontSize: 11, fontWeight: '700' },
})
