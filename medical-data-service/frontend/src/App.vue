<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  Activity, Database, Plug, TerminalSquare, Play, Loader2, Copy, Check, KeyRound,
  LogOut, Trash2, ShieldCheck,
} from 'lucide-vue-next'
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from '@/components/ui/table'
import {
  fetchConfig, beginLogin, completeLogin, logout, getToken, setToken, currentEmail,
  getInfo, getCatalog, previewSql, runQuery, listKeys, createKey, revokeKey,
  type PublicConfig, type ServerInfo, type CatalogItem, type QueryResult, type ApiKey,
} from '@/api'

// -- auth bootstrap ----------------------------------------------------------
const ready = ref(false)
const config = ref<PublicConfig>()
const loggedIn = ref(false)
const email = ref<string | null>(null)
const isAdmin = computed(() => !!email.value && email.value.toLowerCase() === (config.value?.admin_email ?? '').toLowerCase())

onMounted(async () => {
  config.value = await fetchConfig()
  const params = new URLSearchParams(window.location.search)
  const code = params.get('code')
  if (window.location.pathname === '/callback' && code) {
    try {
      const idToken = await completeLogin(config.value, code)
      setToken(idToken)
    } catch (e) {
      console.error('login failed', e)
    }
    window.history.replaceState({}, '', '/')
  }
  loggedIn.value = !!getToken()
  email.value = currentEmail()
  ready.value = true
  if (loggedIn.value) void loadAll()
})

function doLogin() {
  if (config.value) beginLogin(config.value)
}
function doLogout() {
  if (config.value) logout(config.value)
}

// -- data --------------------------------------------------------------------
const info = ref<ServerInfo>()
const metrics = ref<CatalogItem[]>([])
const dimensions = ref<CatalogItem[]>([])

async function loadAll() {
  try {
    info.value = await getInfo()
    const catalog = await getCatalog()
    metrics.value = catalog.metrics
    dimensions.value = catalog.dimensions
    if (isAdmin.value) await loadKeys()
  } catch (e) {
    if (String(e).includes('unauthorized')) { loggedIn.value = false }
  }
}

// The endpoint lives at /mcp/ (trailing slash); without it a client hits the
// SPA catch-all and gets 405 instead of the 401 that starts OAuth.
const mcpUrl = computed(() => `${window.location.origin}${info.value?.mcp_path ?? '/mcp'}/`)
const isOAuth = computed(() => config.value?.mcp_auth_mode === 'oauth')

// OAuth mode: agents connect via mcp-remote with the pre-created public
// client_id (Cognito has no DCR). API-key mode: X-API-Key header.
const mcpConfig = computed(() => {
  if (isOAuth.value) {
    return JSON.stringify(
      {
        mcpServers: {
          'medical-data-service': {
            command: 'npx',
            args: [
              '-y', 'mcp-remote', mcpUrl.value, '3334',
              // client-info (not client-metadata): supplies a pre-registered
              // client_id and skips DCR, which Cognito does not support.
              '--static-oauth-client-info',
              JSON.stringify({ client_id: config.value?.mcp_agent_client_id ?? '', token_endpoint_auth_method: 'none' }),
            ],
          },
        },
      }, null, 2,
    )
  }
  return JSON.stringify(
    { mcpServers: { 'medical-data-service': { url: mcpUrl.value, transport: 'http', headers: { 'X-API-Key': '<你的 API Key>' } } } },
    null, 2,
  )
})

const copied = ref('')
async function copy(text: string, key: string) {
  await navigator.clipboard.writeText(text)
  copied.value = key
  setTimeout(() => (copied.value = ''), 1500)
}

// -- playground --------------------------------------------------------------
const selMetrics = ref('net_sales_amount')
const selGroupBy = ref('sales_region__sales_region_name')
const result = ref<QueryResult>()
const busy = ref(false)
const runError = ref('')
const parse = (v: string) => v.split(',').map((s) => s.trim()).filter(Boolean)

async function doPreview() {
  busy.value = true; runError.value = ''
  try { result.value = await previewSql(parse(selMetrics.value), parse(selGroupBy.value)) }
  catch (e) { runError.value = String(e) } finally { busy.value = false }
}
async function doRun() {
  busy.value = true; runError.value = ''
  try { result.value = await runQuery(parse(selMetrics.value), parse(selGroupBy.value), 100) }
  catch (e) { runError.value = String(e) } finally { busy.value = false }
}

// -- API keys ----------------------------------------------------------------
const keys = ref<ApiKey[]>([])
const newKeyName = ref('')
const createdKey = ref('')
const keyBusy = ref(false)
const keyError = ref('')

async function loadKeys() {
  try { keys.value = await listKeys() } catch (e) { keyError.value = String(e) }
}
async function doCreateKey() {
  keyBusy.value = true; keyError.value = ''
  try {
    const r = await createKey(newKeyName.value)
    createdKey.value = r.api_key
    newKeyName.value = ''
    await loadKeys()
  } catch (e) { keyError.value = String(e) } finally { keyBusy.value = false }
}
async function doRevoke(id: string) {
  keyBusy.value = true; keyError.value = ''
  try { await revokeKey(id); await loadKeys() }
  catch (e) { keyError.value = String(e) } finally { keyBusy.value = false }
}
</script>

<template>
  <div class="min-h-screen">
    <!-- loading -->
    <div v-if="!ready" class="flex min-h-screen items-center justify-center">
      <Loader2 class="size-8 animate-spin text-muted-foreground" />
    </div>

    <!-- login gate -->
    <div v-else-if="!loggedIn" class="flex min-h-screen items-center justify-center px-4">
      <Card class="w-full max-w-md">
        <CardHeader>
          <CardTitle class="flex items-center gap-2"><Database class="size-5 text-primary" />医药销售 MCP 数据服务</CardTitle>
          <CardDescription>管理页面需要登录。使用公司 Cognito 账号访问。</CardDescription>
        </CardHeader>
        <CardContent>
          <Button class="w-full" @click="doLogin"><ShieldCheck class="mr-1 size-4" />用 Cognito 登录</Button>
        </CardContent>
      </Card>
    </div>

    <!-- app -->
    <template v-else>
      <header class="border-b">
        <div class="mx-auto flex max-w-6xl items-center justify-between px-6 py-4">
          <div class="flex items-center gap-3">
            <Database class="size-6 text-primary" />
            <div>
              <h1 class="text-lg font-semibold">医药销售 MCP 数据服务</h1>
              <p class="text-xs text-muted-foreground">语义层驱动的远程 MCP · 自然语言问数与分析</p>
            </div>
          </div>
          <div class="flex items-center gap-2">
            <Badge v-if="info" variant="success" class="gap-1"><Activity class="size-3" /> 在线</Badge>
            <Badge v-if="info" :variant="info.execution_enabled ? 'default' : 'secondary'">
              {{ info.execution_enabled ? '查询已启用' : '仅预览 SQL' }}
            </Badge>
            <span class="ml-2 text-sm text-muted-foreground">{{ email }}</span>
            <Button size="sm" variant="outline" @click="doLogout"><LogOut class="mr-1 size-4" />登出</Button>
          </div>
        </div>
      </header>

      <main class="mx-auto max-w-6xl px-6 py-8">
        <Tabs default-value="connect" class="w-full">
          <TabsList>
            <TabsTrigger value="connect"><Plug class="mr-1 size-4" />连接 MCP</TabsTrigger>
            <TabsTrigger value="catalog"><Database class="mr-1 size-4" />指标与维度</TabsTrigger>
            <TabsTrigger value="playground"><TerminalSquare class="mr-1 size-4" />查询实验台</TabsTrigger>
            <TabsTrigger v-if="isAdmin && !isOAuth" value="keys"><KeyRound class="mr-1 size-4" />API 密钥</TabsTrigger>
          </TabsList>

          <!-- Connect -->
          <TabsContent value="connect">
            <div class="grid gap-4 md:grid-cols-2">
              <Card>
                <CardHeader>
                  <CardTitle>远程 MCP 地址</CardTitle>
                  <CardDescription>{{ isOAuth ? '本地 agent 连这个 URL，通过 Cognito OAuth 登录' : '本地 agent 连这个 URL，并在请求头带 API Key' }}</CardDescription>
                </CardHeader>
                <CardContent class="space-y-4">
                  <div class="flex items-center gap-2">
                    <code class="flex-1 truncate rounded-md bg-muted px-3 py-2 text-sm">{{ mcpUrl }}</code>
                    <Button size="icon" variant="outline" @click="copy(mcpUrl, 'url')">
                      <Check v-if="copied === 'url'" class="size-4" /><Copy v-else class="size-4" />
                    </Button>
                  </div>
                  <div class="text-sm text-muted-foreground">传输：<Badge variant="outline">{{ info?.transport }}</Badge>　鉴权：<Badge variant="outline">{{ isOAuth ? 'Cognito OAuth（每人各自登录）' : 'API Key（X-API-Key 头）' }}</Badge></div>
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle>Amazon Q / Claude 配置</CardTitle>
                  <CardDescription>{{ isOAuth ? '连接时会弹出 Cognito 登录，登录后即以你本人身份取数' : '把 <你的 API Key> 换成「API 密钥」页创建的 key' }}</CardDescription>
                </CardHeader>
                <CardContent>
                  <div class="relative">
                    <pre class="overflow-auto rounded-md bg-muted p-3 text-xs"><code>{{ mcpConfig }}</code></pre>
                    <Button size="icon" variant="outline" class="absolute right-2 top-2" @click="copy(mcpConfig, 'cfg')">
                      <Check v-if="copied === 'cfg'" class="size-4" /><Copy v-else class="size-4" />
                    </Button>
                  </div>
                </CardContent>
              </Card>

              <Card class="md:col-span-2">
                <CardHeader>
                  <CardTitle>可用工具</CardTitle>
                  <CardDescription>连接后 agent 可调用以下工具，用自然语言问数分析</CardDescription>
                </CardHeader>
                <CardContent>
                  <div class="grid gap-3 sm:grid-cols-2">
                    <div v-for="t in info?.tools" :key="t.name" class="rounded-lg border p-3">
                      <code class="text-sm font-semibold text-primary">{{ t.name }}</code>
                      <p class="mt-1 text-sm text-muted-foreground">{{ t.description }}</p>
                    </div>
                  </div>
                </CardContent>
              </Card>
            </div>
          </TabsContent>

          <!-- Catalog -->
          <TabsContent value="catalog">
            <div class="grid gap-4 lg:grid-cols-2">
              <Card>
                <CardHeader><CardTitle>指标 <Badge variant="secondary">{{ metrics.length }}</Badge></CardTitle><CardDescription>可测量的业务口径</CardDescription></CardHeader>
                <CardContent>
                  <Table>
                    <TableHeader><TableRow><TableHead>名称</TableHead><TableHead>说明</TableHead></TableRow></TableHeader>
                    <TableBody>
                      <TableRow v-for="m in metrics" :key="m.name">
                        <TableCell><code class="text-xs text-primary">{{ m.name }}</code></TableCell>
                        <TableCell class="text-muted-foreground">{{ m.description }}</TableCell>
                      </TableRow>
                    </TableBody>
                  </Table>
                </CardContent>
              </Card>
              <Card>
                <CardHeader><CardTitle>维度 <Badge variant="secondary">{{ dimensions.length }}</Badge></CardTitle><CardDescription>可用于分组切片的字段</CardDescription></CardHeader>
                <CardContent>
                  <Table>
                    <TableHeader><TableRow><TableHead>名称</TableHead><TableHead>说明</TableHead></TableRow></TableHeader>
                    <TableBody>
                      <TableRow v-for="d in dimensions" :key="d.name">
                        <TableCell><code class="text-xs text-primary">{{ d.name }}</code></TableCell>
                        <TableCell class="text-muted-foreground">{{ d.description }}</TableCell>
                      </TableRow>
                    </TableBody>
                  </Table>
                </CardContent>
              </Card>
            </div>
          </TabsContent>

          <!-- Playground -->
          <TabsContent value="playground">
            <Card>
              <CardHeader><CardTitle>查询实验台</CardTitle><CardDescription>手动验证语义层查询（agent 走 MCP 做的就是这件事）</CardDescription></CardHeader>
              <CardContent class="space-y-4">
                <div class="grid gap-3 sm:grid-cols-2">
                  <div><label class="mb-1 block text-sm font-medium">指标（逗号分隔）</label><Input v-model="selMetrics" placeholder="net_sales_amount, order_count" /></div>
                  <div><label class="mb-1 block text-sm font-medium">分组维度（逗号分隔）</label><Input v-model="selGroupBy" placeholder="sales_region__sales_region_name" /></div>
                </div>
                <div class="flex gap-2">
                  <Button variant="outline" :disabled="busy" @click="doPreview"><Loader2 v-if="busy" class="mr-1 size-4 animate-spin" /><TerminalSquare v-else class="mr-1 size-4" />预览 SQL</Button>
                  <Button :disabled="busy || !info?.execution_enabled" @click="doRun"><Loader2 v-if="busy" class="mr-1 size-4 animate-spin" /><Play v-else class="mr-1 size-4" />执行查询</Button>
                </div>
                <p v-if="runError" class="rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">{{ runError }}</p>
                <div v-if="result?.sql">
                  <label class="mb-1 block text-sm font-medium">生成的 SQL</label>
                  <pre class="overflow-auto rounded-md bg-muted p-3 text-xs"><code>{{ result.sql }}</code></pre>
                </div>
                <div v-if="result?.rows?.length">
                  <label class="mb-1 block text-sm font-medium">结果 <Badge variant="secondary">{{ result.row_count }} 行</Badge></label>
                  <Table>
                    <TableHeader><TableRow><TableHead v-for="c in result.columns" :key="c">{{ c }}</TableHead></TableRow></TableHeader>
                    <TableBody><TableRow v-for="(row, i) in result.rows" :key="i"><TableCell v-for="c in result.columns" :key="c">{{ row[c] }}</TableCell></TableRow></TableBody>
                  </Table>
                </div>
              </CardContent>
            </Card>
          </TabsContent>

          <!-- API keys (admin only) -->
          <TabsContent v-if="isAdmin" value="keys">
            <Card>
              <CardHeader>
                <CardTitle>API 密钥管理</CardTitle>
                <CardDescription>给 MCP 端点用的访问密钥。明文只在创建时显示一次，之后仅存哈希。</CardDescription>
              </CardHeader>
              <CardContent class="space-y-4">
                <div class="flex gap-2">
                  <Input v-model="newKeyName" placeholder="密钥名称，如 kolya-laptop" class="max-w-xs" />
                  <Button :disabled="keyBusy" @click="doCreateKey"><Loader2 v-if="keyBusy" class="mr-1 size-4 animate-spin" /><KeyRound v-else class="mr-1 size-4" />创建密钥</Button>
                </div>

                <div v-if="createdKey" class="rounded-md border border-emerald-600/40 bg-emerald-600/10 p-3">
                  <p class="mb-2 text-sm font-medium text-emerald-500">新密钥已创建，请立即复制保存（只显示这一次）：</p>
                  <div class="flex items-center gap-2">
                    <code class="flex-1 break-all rounded bg-background px-3 py-2 text-sm">{{ createdKey }}</code>
                    <Button size="icon" variant="outline" @click="copy(createdKey, 'newkey')"><Check v-if="copied === 'newkey'" class="size-4" /><Copy v-else class="size-4" /></Button>
                    <Button size="sm" variant="ghost" @click="createdKey = ''">我已保存</Button>
                  </div>
                </div>

                <p v-if="keyError" class="rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">{{ keyError }}</p>

                <Table>
                  <TableHeader><TableRow><TableHead>名称</TableHead><TableHead>密钥</TableHead><TableHead>创建时间</TableHead><TableHead>状态</TableHead><TableHead></TableHead></TableRow></TableHeader>
                  <TableBody>
                    <TableRow v-for="k in keys" :key="k.id">
                      <TableCell>{{ k.name }}</TableCell>
                      <TableCell><code class="text-xs">{{ k.masked_key }}</code></TableCell>
                      <TableCell class="text-muted-foreground">{{ new Date(k.created_at).toLocaleString() }}</TableCell>
                      <TableCell><Badge :variant="k.active ? 'success' : 'secondary'">{{ k.active ? '有效' : '已吊销' }}</Badge></TableCell>
                      <TableCell>
                        <Button v-if="k.active" size="sm" variant="ghost" :disabled="keyBusy" @click="doRevoke(k.id)"><Trash2 class="mr-1 size-4" />吊销</Button>
                      </TableCell>
                    </TableRow>
                    <TableRow v-if="!keys.length"><TableCell class="text-muted-foreground" colspan="5">还没有密钥。创建一个给 agent 使用。</TableCell></TableRow>
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          </TabsContent>
        </Tabs>
      </main>
    </template>
  </div>
</template>
