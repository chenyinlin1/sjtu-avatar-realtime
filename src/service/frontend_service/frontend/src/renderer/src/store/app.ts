import { message } from 'ant-design-vue'
import { defineStore } from 'pinia'

import { createWebPersona, initConfig, listWebPersonas, makeURL } from '@/apis'
import { useMediaStore } from './media'
import { TextPayload } from '@renderer/interface/eventType'

type ChatRecord = {
  id: string
  role: 'human' | 'avatar'
  message: string
  cancelled?: boolean
  invalid?: boolean
} & TextPayload

type PersonaAssetStatus = 'NONE' | 'PROCESSING' | 'READY' | 'FAILED' | string

type PersonaAsset = {
  status: PersonaAssetStatus
  voice_id?: string | null
  model_name?: string | null
  image_path?: string | null
  fail_reason?: string | null
}

export type WebPersonaRecord = {
  persona_id: string
  elder_id: string
  tenant_id: string
  relationship?: string | null
  display_name: string
  address_to_elder?: string | null
  self_reference?: string | null
  gender?: string | null
  persona_prompt?: string | null
  is_default: boolean
  status: string
  voice: PersonaAsset
  face: PersonaAsset
}

interface AppState {
  avatarType: '' | 'lam'
  avatarWSRoute: string
  wsSessionRoute: string
  avatarAssetsPath: string
  rtcConfig: RTCConfiguration | undefined
  chatMode: 'webrtc' | 'ws'
  chatRecords: ChatRecord[]

  toolsVisible: boolean
  inputVisible: boolean
  avatarCloneEnabled: boolean
  avatarCloneUploadRoute: string
  voiceCloneEnabled: boolean
  voiceCloneUploadRoute: string
  voiceCloneResetRoute: string
  voiceCloneSampleText: string

  webPersonaEnabled: boolean
  webPersonaListRoute: string
  webPersonaCreateRoute: string
  webPersonaDeviceSn: string
  webPersonaItems: WebPersonaRecord[]
  selectedPersonaId: string
}

const SELECTED_PERSONA_STORAGE_KEY = 'openavatarchat_selected_persona_id'

function unwrapPayload(payload: any): any {
  return payload?.data || payload
}

export const useAppStore = defineStore('appStore', {
  state: (): AppState => ({
    avatarType: '',
    avatarWSRoute: '',
    wsSessionRoute: '',
    avatarAssetsPath: '',
    rtcConfig: undefined,
    chatMode: 'webrtc',
    chatRecords: [],
    toolsVisible: true,
    inputVisible: true,
    avatarCloneEnabled: false,
    avatarCloneUploadRoute: '',
    voiceCloneEnabled: false,
    voiceCloneUploadRoute: '',
    voiceCloneResetRoute: '',
    voiceCloneSampleText: '',
    webPersonaEnabled: false,
    webPersonaListRoute: '',
    webPersonaCreateRoute: '',
    webPersonaDeviceSn: 'web_frontend',
    webPersonaItems: [],
    selectedPersonaId: '',
  }),
  getters: {
    selectedPersona(state): WebPersonaRecord | null {
      return state.webPersonaItems.find((item) => item.persona_id === state.selectedPersonaId) || null
    },
  },
  actions: {
    async init() {
      const mediaStore = useMediaStore()
      return initConfig()
        .then((res) => res.json())
        .then(async (config) => {
          if (config.detail) {
            message.error(config.detail)
            return
          }
          if (config.rtc_configuration) {
            this.rtcConfig = config.rtc_configuration
          }
          if (config.chat_mode) {
            this.chatMode = config.chat_mode === 'ws' ? 'ws' : 'webrtc'
          }
          this.avatarCloneEnabled = Boolean(config.avatar_clone?.enabled)
          this.avatarCloneUploadRoute = config.avatar_clone?.upload_route || ''
          this.voiceCloneEnabled = Boolean(config.voice_clone?.enabled)
          this.voiceCloneUploadRoute = config.voice_clone?.upload_route || ''
          this.voiceCloneResetRoute = config.voice_clone?.reset_route || ''
          this.voiceCloneSampleText = config.voice_clone?.sample_text || ''

          const webPersona = config.web_persona || {}
          this.webPersonaEnabled = Boolean(webPersona.enabled)
          this.webPersonaListRoute = webPersona.list_route || ''
          this.webPersonaCreateRoute = webPersona.create_route || ''
          this.webPersonaDeviceSn = webPersona.device_sn || 'web_frontend'
          if (this.webPersonaEnabled) {
            this.avatarCloneEnabled = Boolean(webPersona.avatar_clone_enabled)
            this.voiceCloneEnabled = Boolean(webPersona.voice_clone_enabled)
            this.avatarCloneUploadRoute = webPersona.face_upload_route_template || this.avatarCloneUploadRoute
            this.voiceCloneUploadRoute = webPersona.voice_upload_route_template || this.voiceCloneUploadRoute
            this.voiceCloneResetRoute = webPersona.voice_reset_route_template || this.voiceCloneResetRoute
            this.voiceCloneSampleText = webPersona.sample_text || this.voiceCloneSampleText
          }

          config.avatar_config = config.avatar_config || {}
          if (config.avatar_config) {
            this.avatarType = config.avatar_config.avatar_type || ''
            this.avatarWSRoute = config.avatar_config.avatar_ws_route || ''
            this.avatarAssetsPath = config.avatar_config.avatar_assets_path
              ? makeURL(config.avatar_config.avatar_assets_path)
              : ''
            if (config.avatar_config.ws_session_route) {
              this.wsSessionRoute = config.avatar_config.ws_session_route
              if (!this.avatarWSRoute) {
                this.avatarWSRoute = config.avatar_config.ws_session_route
              }
            }
          }
          if (config.ws_session_route) {
            this.wsSessionRoute = config.ws_session_route
            if (!this.avatarWSRoute) {
              this.avatarWSRoute = config.ws_session_route
            }
          }
          if (config.track_constraints) {
            mediaStore.setTrackConstraints(config.track_constraints)
          }
          if (this.webPersonaEnabled) {
            await this.refreshWebPersonas()
          }
        })
        .catch((e) => {
          message.error(
            `服务端链接失败，请检查是否能正确访问到 OpenAvatarChat 服务端: ${e instanceof Error ? e.message : String(e)}`
          )
        })
    },
    resetChatRecords() {
      this.chatRecords = []
    },
    selectWebPersona(personaId: string) {
      this.selectedPersonaId = personaId
      if (personaId) {
        localStorage.setItem(SELECTED_PERSONA_STORAGE_KEY, personaId)
      } else {
        localStorage.removeItem(SELECTED_PERSONA_STORAGE_KEY)
      }
    },
    async refreshWebPersonas() {
      if (!this.webPersonaEnabled || !this.webPersonaListRoute) return
      const previousSelection = this.selectedPersonaId || localStorage.getItem(SELECTED_PERSONA_STORAGE_KEY) || ''
      const response = await listWebPersonas(this.webPersonaListRoute)
      const rawPayload = await response.json().catch(() => ({}))
      const payload = unwrapPayload(rawPayload)
      if (!response.ok) {
        throw new Error(payload.detail || payload.message || '角色列表加载失败')
      }
      this.webPersonaItems = Array.isArray(payload.items) ? payload.items : []
      const selectedExists = this.webPersonaItems.some((item) => item.persona_id === previousSelection)
      const nextSelection = selectedExists
        ? previousSelection
        : payload.selected_persona_id || payload.default_persona_id || this.webPersonaItems[0]?.persona_id || ''
      this.selectWebPersona(nextSelection)
    },
    async createWebPersona(displayName: string) {
      const cleanedName = displayName.trim()
      if (!cleanedName) throw new Error('请输入角色名称')
      const owner = this.selectedPersona || this.webPersonaItems[0]
      const response = await createWebPersona(this.webPersonaCreateRoute, {
        display_name: cleanedName,
        elder_id: owner?.elder_id,
        tenant_id: owner?.tenant_id,
        relationship: cleanedName,
        self_reference: cleanedName,
        is_default: this.webPersonaItems.length === 0,
      })
      const rawPayload = await response.json().catch(() => ({}))
      const payload = unwrapPayload(rawPayload)
      if (!response.ok) {
        throw new Error(payload.detail || payload.message || '角色创建失败')
      }
      await this.refreshWebPersonas()
      const personaId = payload.persona_id || payload.persona?.persona_id
      if (personaId) this.selectWebPersona(personaId)
    },
    currentDeviceInfoPayload(): Record<string, string> | null {
      if (!this.webPersonaEnabled || !this.selectedPersona) return null
      return {
        device_sn: this.webPersonaDeviceSn,
        elder_id: this.selectedPersona.elder_id,
        tenant_id: this.selectedPersona.tenant_id,
        persona_id: this.selectedPersona.persona_id,
      }
    },
  },
})
