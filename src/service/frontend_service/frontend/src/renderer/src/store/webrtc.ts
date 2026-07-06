import { WS } from '@/helpers/ws'
import type { DeviceInfoPayload, MusicStatusPayload, SignalBody, TextPayload } from '@/interface/eventType'
import { WsEventTypes, WsProtocol } from '@/interface/eventType'
import { StreamState } from '@/interface/voiceChat'
import { AvatarHandler } from '@renderer/handlers/avatarHandler'
import { setupWebRTC, stop } from '@/utils/webrtcUtils'
import { message } from 'ant-design-vue'
import { nanoid } from 'nanoid'
import { defineStore } from 'pinia'
import { createWS } from '@/apis'
import { useAppStore } from './app'
import { useChatStore } from './chat'
import { useMediaStore } from './media'
import { useVisionStore } from './vision'
import { watch } from 'vue'

interface VideoChatState {
  streamState: StreamState
  peerConnection: RTCPeerConnection | null
  webRTCId: string
  gsLoadPercent: number
  localAvatarRenderer: AvatarHandler | null
  chatDataChannel: RTCDataChannel | null
}

export const useVideoChatStore = defineStore('videoChatStore', {
  state: (): VideoChatState => {
    return {
      streamState: StreamState.closed,
      peerConnection: null,
      webRTCId: '',
      gsLoadPercent: 0,
      localAvatarRenderer: null,
      chatDataChannel: null,
    }
  },
  getters: {},
  actions: {
    async startWebRTC() {
      const visionState = useVisionStore()
      const mediaStore = useMediaStore()
      const appStore = useAppStore()
      const chatStore = useChatStore()
      if (this.streamState === 'closed') {
        appStore.resetChatRecords()
        this.peerConnection = new RTCPeerConnection(appStore.rtcConfig)
        this.peerConnection.addEventListener('connectionstatechange', async () => {
          switch (this.peerConnection!.connectionState) {
            case 'connected':
              this.streamState = StreamState.open
              break
            case 'disconnected':
              this.streamState = StreamState.closed
              stop(this.peerConnection!)
              chatStore.setMusicStatusSender(null)
              this.chatDataChannel = null
              break
            default:
              break
          }
        })
        this.streamState = StreamState.waiting
        await setupWebRTC(mediaStore.stream!, this.peerConnection!, visionState.remoteVideoRef!)
          .then(([dataChannel, webRTCId]) => {
            this.streamState = StreamState.open
            this.webRTCId = webRTCId as string
            this.chatDataChannel = dataChannel as RTCDataChannel
            chatStore.setMusicStatusSender((payload) => this.sendMusicStatus(payload))
            this.initChatDataChannel()
            this.sendDeviceInfo()

            if (appStore.avatarType === 'lam') {
              if (appStore.wsSessionRoute) {
                const ws = this.initWebsocket(appStore.wsSessionRoute, this.webRTCId)
                this.localAvatarRenderer = this.initAvatarHandler(ws, appStore.avatarAssetsPath)
                chatStore.showChatRecords = true
              }
            }
          })
          .catch((e: unknown) => {
            console.info('catching', e)
            this.streamState = StreamState.closed
            chatStore.setMusicStatusSender(null)
            const errorMessage = e instanceof Error ? e.message : String(e)
            message.error(errorMessage)
            message.error('请检查是否超过数字人并发上限')
          })
      } else if (this.streamState === 'waiting') {
        // waiting 中不允许操作
      } else {
        stop(this.peerConnection!)
        this.streamState = StreamState.closed
        appStore.resetChatRecords()
        this.chatDataChannel = null
        chatStore.setMusicStatusSender(null)
        chatStore.replying = false
        await mediaStore.accessDevice()
        if (appStore.avatarType === 'lam') {
          this.localAvatarRenderer?.exit()
          if (this.localAvatarRenderer instanceof AvatarHandler) {
            this.localAvatarRenderer.removeAllListeners()
          }
          this.localAvatarRenderer = null
          chatStore.setActiveRenderer(null)
          this.gsLoadPercent = 0
        }
      }
    },

    sendDeviceInfo() {
      const appStore = useAppStore()
      const payload = appStore.currentDeviceInfoPayload() as DeviceInfoPayload | null
      if (!payload || !this.chatDataChannel) return
      const send = (): void => {
        if (!this.chatDataChannel || this.chatDataChannel.readyState !== 'open') return
        this.chatDataChannel.send(
          JSON.stringify({
            header: {
              name: WsProtocol.DeviceInfo,
              request_id: nanoid(),
            },
            payload,
          })
        )
      }
      if (this.chatDataChannel.readyState === 'open') {
        send()
      } else {
        this.chatDataChannel.addEventListener('open', send, { once: true })
      }
    },
    sendMusicStatus(payload: MusicStatusPayload): boolean {
      if (!this.chatDataChannel || this.chatDataChannel.readyState !== 'open') {
        console.info('[music] MusicStatus skipped: data channel is not open', payload)
        return false
      }
      this.chatDataChannel.send(
        JSON.stringify({
          header: {
            name: WsProtocol.MusicStatus,
            request_id: nanoid(),
          },
          payload,
        })
      )
      console.info('[music] MusicStatus sent', payload)
      return true
    },
    sendText(text: string) {
      if (!text || !this.chatDataChannel) return
      const chatStore = useChatStore()
      this.chatDataChannel.send(
        JSON.stringify({
          header: {
            name: WsProtocol.SendHumanText,
            request_id: nanoid(),
          },
          payload: {
            request_id: nanoid(),
            stream_key: nanoid(),
            mode: 'full_text',
            text,
            end_of_speech: true,
          },
        })
      )
      console.log('sendText', text)
      chatStore.replying = true
    },
    interrupt() {
      const chatStore = useChatStore()
      console.log('interrupt')
      const appStore = useAppStore()
      if (appStore.avatarType === 'lam') {
        this.localAvatarRenderer?.interrupt()
        chatStore.replying = false
      } else if (this.chatDataChannel) {
        this.chatDataChannel.send(
          JSON.stringify({
            header: {
              name: WsProtocol.Interrupt,
              request_id: nanoid(),
            },
            payload: {},
          })
        )
      }
    },
    initChatDataChannel() {
      if (!this.chatDataChannel) return
      const chatStore = useChatStore()
      this.chatDataChannel.addEventListener('message', (event) => {
        const data = JSON.parse(event.data)
        const headerName = data?.header?.name as WsProtocol | undefined
        if (headerName === WsProtocol.EchoHumanText || headerName === WsProtocol.EchoAvatarText) {
          const payload = (data.payload || {}) as TextPayload
          const consumedClientAction = chatStore.handleClientAction(
            payload as Parameters<typeof chatStore.handleClientAction>[0]
          )
          if (consumedClientAction) {
            console.info('[music] WebRTC data channel consumed client_action', {
              headerName,
              streamKey: payload.stream_key,
              hasText: Boolean(payload.text),
              endOfSpeech: payload.end_of_speech,
            })
          }
          if (typeof payload.text !== 'string') return
          if (consumedClientAction && !payload.text) return
          const role = headerName === WsProtocol.EchoAvatarText ? 'avatar' : 'human'
          chatStore.updateChatRecords({ ...payload, role }, role)
        } else if (headerName === WsProtocol.DeviceInfoAck) {
          console.info('[persona] DeviceInfoAck', data.payload)
        } else if (headerName === WsProtocol.Error) {
          const payload = (data.payload || {}) as { message?: string }
          if (payload.message) message.warning(payload.message)
        } else if (
          headerName === WsProtocol.InterruptNotification ||
          headerName === WsProtocol.EndSpeech
        ) {
          chatStore.replying = false
        } else if (headerName === WsProtocol.ChatSignal) {
          chatStore.handleChatSignal((data.payload || {}) as SignalBody)
        }
      })
    },
    initWebsocket(ws_route: string, webRTCId: string): WS {
      const ws = createWS(ws_route, webRTCId)
      ws.on(WsEventTypes.WS_OPEN, () => {
        console.log('socket opened')
      })
      ws.on(WsEventTypes.WS_CLOSE, () => {
        console.log('socket closed')
      })
      ws.on(WsEventTypes.WS_ERROR, (event) => {
        console.log('socket error', event)
      })
      ws.on(WsEventTypes.WS_MESSAGE, (data) => {
        console.log('socket on message', data)
      })
      return ws
    },
    initAvatarHandler(ws: WS, assetsPath: string): AvatarHandler {
      const visionState = useVisionStore()
      const chatStore = useChatStore()
      const handler = new AvatarHandler({
        container: visionState.remoteVideoContainerRef!,
        assetsPath,
        ws,
        rendererType: 'lam',
        loadProgress: (progress) => {
          console.log('gs loadProgress', progress)
          this.gsLoadPercent = progress
        },
      })

      chatStore.bindAvatarHandler(handler)
      chatStore.setActiveRenderer(handler)

      return handler
    },
  },
})

export function setupElectron(): void {
  if (window.electron) {
    const chatStore = useChatStore()
    window.electron.ipcRenderer.on('state-changed', (_event, data) => {
      void _event
      console.log('🚀 ~ state-changed:', data)
      const { key, value } = data
      const appStore = useAppStore()
      if (key in appStore.$state) {
        ;(appStore as unknown as Record<string, unknown>)[key] = value
      } else if (key in chatStore.$state) {
        ;(chatStore as unknown as Record<string, unknown>)[key] = value
      } else {
        const videoChatStore = useVideoChatStore()
        ;(videoChatStore as unknown as Record<string, unknown>)[key] = value
      }
    })
    window.electron.ipcRenderer.send('app-ready')
    watch(
      () => chatStore.showChatRecords,
      (newValue) => {
        console.log('🚀 ~ newValue:', newValue)
        window.electron.ipcRenderer.send('state-changed', {
          key: 'showChatRecords',
          value: newValue,
        })
      }
    )
    window.addEventListener('resize', () => {
      if (chatStore.showChatRecords) {
        chatStore.updateWrapperRect()
      }
    })
  }
}
