import { defineStore } from 'pinia'
import { nanoid } from 'nanoid'
import { markRaw } from 'vue'

import EventEmitter from 'eventemitter3'

import { EventTypes, SignalBody, TextPayload } from '@/interface/eventType'
import { TYVoiceChatState } from '@/interface/voiceChat'

import { useAppStore } from './app'
import { useVisionStore } from './vision'

interface AvatarLike {
  setAvatarMute?(isMute: boolean): void
  interrupt?(): void
}

type MusicClientAction =
  | {
      type: 'music.play'
      title?: string
      artist?: string
      url?: string
      source?: string
      query?: string
      candidates?: Array<Record<string, unknown>>
    }
  | {
      type: 'music.control'
      action?: 'pause' | 'resume' | 'next' | 'volume' | 'mute' | 'unmute' | 'stop' | string
      delta?: number
    }

interface TextPayloadWithClientAction extends Partial<TextPayload> {
  metadata?: TextPayload['metadata'] & {
    client_action?: MusicClientAction
  }
}

interface ChatState {
  volumeMuted: boolean
  showChatRecords: boolean
  replying: boolean
  activeRenderer: AvatarLike | null
  musicAudio: HTMLAudioElement | null
  musicVolume: number
  musicMuted: boolean
}

export const useChatStore = defineStore('chatStore', {
  state: (): ChatState => ({
    volumeMuted: false,
    showChatRecords: false,
    replying: false,
    activeRenderer: null,
    musicAudio: null,
    musicVolume: 1,
    musicMuted: false,
  }),
  actions: {
    setActiveRenderer(renderer: AvatarLike | null) {
      this.activeRenderer = renderer
    },

    handleVolumeMute() {
      this.volumeMuted = !this.volumeMuted
      this.activeRenderer?.setAvatarMute?.(this.volumeMuted)
    },

    handleSubtitleToggle() {
      this.showChatRecords = !this.showChatRecords
      this.updateWrapperRect()
    },

    updateWrapperRect() {
      const visionState = useVisionStore()
      const { wrapperRef, wrapperRect } = visionState
      if (!wrapperRef || !wrapperRect) return
      wrapperRef.getBoundingClientRect()
      wrapperRect.width = wrapperRef.clientWidth
      wrapperRect.height = wrapperRef.clientHeight
      visionState.isLandscape = wrapperRect.width > wrapperRect.height
    },

    getClientAction(payload?: TextPayloadWithClientAction): MusicClientAction | undefined {
      const action = payload?.metadata?.client_action
      if (!action || typeof action !== 'object' || typeof action.type !== 'string') return undefined
      return action
    },

    handleClientAction(payload?: TextPayloadWithClientAction): boolean {
      const action = this.getClientAction(payload)
      if (!action) return false
      if (action.type === 'music.play') {
        this.playMusicAction(action)
        return true
      }
      if (action.type === 'music.control') {
        this.controlMusicAction(action)
        return true
      }
      return false
    },

    playMusicAction(action: Extract<MusicClientAction, { type: 'music.play' }>) {
      if (!action.url) {
        console.warn('music.play action missing url', action)
        return
      }
      if (this.musicAudio) {
        this.musicAudio.pause()
        this.musicAudio.src = ''
      }
      const audio = markRaw(new Audio(action.url))
      audio.preload = 'auto'
      audio.volume = this.musicVolume
      audio.muted = this.musicMuted
      audio.addEventListener('ended', () => {
        if (this.musicAudio === audio) {
          this.musicAudio = null
        }
      })
      this.musicAudio = audio
      audio.play().catch((e) => {
        console.error('music.play failed', e)
      })
    },

    controlMusicAction(action: Extract<MusicClientAction, { type: 'music.control' }>) {
      const audio = this.musicAudio
      switch (action.action) {
        case 'pause':
          audio?.pause()
          break
        case 'resume':
          audio?.play().catch((e) => {
            console.error('music.resume failed', e)
          })
          break
        case 'stop':
          if (audio) {
            audio.pause()
            audio.src = ''
          }
          this.musicAudio = null
          break
        case 'next':
          if (audio) {
            audio.pause()
            if (Number.isFinite(audio.duration)) {
              audio.currentTime = audio.duration
            }
          }
          this.musicAudio = null
          break
        case 'volume': {
          const delta = typeof action.delta === 'number' ? action.delta : 0
          this.musicVolume = Math.min(1, Math.max(0, this.musicVolume + delta))
          if (audio) audio.volume = this.musicVolume
          break
        }
        case 'mute':
          this.musicMuted = true
          if (audio) audio.muted = true
          break
        case 'unmute':
          this.musicMuted = false
          if (audio) audio.muted = false
          break
        default:
          console.warn('Unsupported music.control action', action)
      }
    },

    updateChatRecords(
      payload: Partial<TextPayload> & Record<string, unknown>,
      role: 'human' | 'avatar'
    ) {
      const appStore = useAppStore()
      const streamKey =
        (payload?.stream_key as string) || (payload?.request_id as string) || nanoid()
      const id = `${role}-${streamKey}`
      const continueFromStream = (
        payload?.metadata as { continue_from_stream?: unknown } | undefined
      )?.continue_from_stream
      if (continueFromStream !== undefined && continueFromStream !== null) {
        const prevIndex = appStore.chatRecords.findLastIndex(
          (item) => item.role === role && item.id !== id
        )
        if (prevIndex >= 0) {
          const prev = appStore.chatRecords[prevIndex]
          appStore.chatRecords.splice(prevIndex, 1, {
            ...prev,
            invalid: true,
          })
          appStore.chatRecords = [...appStore.chatRecords]
        }
      }
      const index = appStore.chatRecords.findIndex((item) => item.id === id)
      const content = payload?.text || ''
      if (index !== -1) {
        const target = appStore.chatRecords[index]
        target.message = payload?.mode === 'increment' ? target.message + content : content
        Object.assign(target, payload)
        target.role = role
        appStore.chatRecords.splice(index, 1, target)
        appStore.chatRecords = [...appStore.chatRecords]
      } else {
        console.log('updateChatRecords new record', payload)
        if (!content) {
          console.error('updateChatRecords new record content is empty', payload)
        }
        appStore.chatRecords = [
          ...appStore.chatRecords,
          {
            id,
            role,
            message: content,
            ...(payload as TextPayload),
          },
        ]
      }
    },

    markStreamCancelled(streamKey?: string) {
      if (!streamKey) return
      const appStore = useAppStore()
      const index = appStore.chatRecords.findIndex((item) => item.stream_key === streamKey)
      if (index === -1) return
      const target = appStore.chatRecords[index]
      appStore.chatRecords.splice(index, 1, {
        ...target,
        cancelled: true,
      })
      appStore.chatRecords = [...appStore.chatRecords]
    },

    handleChatSignal(signal?: SignalBody) {
      if (!signal) return
      if (signal.type === 'stream_cancel') {
        const keys = [signal.stream_key, ...(signal.parent_stream_keys || [])].filter(
          (key): key is string => Boolean(key)
        )
        const uniqueKeys = new Set(keys)
        uniqueKeys.forEach((key) => this.markStreamCancelled(key))
      }
      console.log('handleChatSignal', signal)
      if (
        (signal.type === 'stream_cancel' || signal.type === 'stream_end') &&
        signal.stream_type === 'client_playback'
      ) {
        this.replying = false
      } else if (signal.type === 'stream_begin' && signal.stream_type === 'client_playback') {
        this.replying = true
      }
    },

    bindAvatarHandler(handler: EventEmitter) {
      handler.on(EventTypes.StateChanged, (state: TYVoiceChatState) => {
        if (state === TYVoiceChatState.Idle) {
          this.replying = false
        }
      })

      handler.on(EventTypes.MessageReceived, (data) => {
        const eventData = data as {
          role?: 'human' | 'avatar'
          payload?: Partial<TextPayload>
        }
        const { payload, role } = eventData || {}
        if (!payload) return
        const consumedClientAction = this.handleClientAction(payload as TextPayloadWithClientAction)
        if (typeof payload.text !== 'string') return
        if (consumedClientAction && !payload.text) return

        this.updateChatRecords(
          { ...payload, role: role === 'human' ? 'human' : 'avatar' },
          role === 'human' ? 'human' : 'avatar'
        )
      })

      handler.on(EventTypes.SignalReceived, (data) => {
        this.handleChatSignal(data as SignalBody | undefined)
      })
    },
  },
})
