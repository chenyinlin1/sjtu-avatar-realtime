<script setup lang="ts">
import { ConfigProvider } from 'ant-design-vue'
import { storeToRefs } from 'pinia'
import { onMounted, ref } from 'vue'

import BrandBadge from '@/components/BrandBadge.vue'
import WebcamPermission from '@/components/WebcamPermission.vue'
import { antdLocale, locale } from '@/langs'
import VideoChat from '@/views/VideoChat/index.vue'
import WSVideoChat from './views/WSVideoChat/index.vue'
import { useAppStore } from './store/app'
import { useMediaStore } from './store/media'
import isElectron from './utils/isElectron'

const appState = useAppStore()
const mediaState = useMediaStore()
const appReady = ref(false)
const { chatMode } = storeToRefs(appState)
onMounted(async () => {
  await appState.init()
  appReady.value = true
})
// import dayjs from 'dayjs';
// import 'dayjs/locale/zh-cn';
// dayjs.locale('zh-cn');
</script>
<template>
  <ConfigProvider :locale="antdLocale[locale]">
    <div
      v-if="isElectron"
      class="wrap wrap-electron"
      :style="{
        backgroundImage: 'none',
      }"
    >
      <BrandBadge />
      <WebcamPermission v-if="appReady && !mediaState.webcamAccessed" auto-access />
      <template v-if="chatMode === 'ws'">
        <WSVideoChat />
      </template>
      <template v-else>
        <VideoChat />
      </template>
    </div>
    <div v-else class="wrap">
      <BrandBadge />
      <WebcamPermission v-if="appReady && !mediaState.webcamAccessed" auto-access />
      <template v-if="chatMode === 'ws'">
        <WSVideoChat />
      </template>
      <template v-else>
        <VideoChat />
      </template>
    </div>
  </ConfigProvider>
</template>
<style lang="less" scoped>
.wrap {
  --flashhead-red: #b01f2e;
  --flashhead-red-dark: #8f1825;
  --flashhead-red-soft: rgba(176, 31, 46, 0.12);
  --flashhead-ink: #1f2937;
  --flashhead-muted: #64748b;
  --flashhead-line: #e5e7eb;
  background:
    linear-gradient(135deg, rgba(248, 250, 252, 0.96) 0%, rgba(255, 255, 255, 0.98) 48%, rgba(248, 244, 245, 0.96) 100%),
    url(@/assets/background.png);
  background-blend-mode: screen;
  height: calc(max(80vh, 100%));
  background-size: 100% 100%;
  background-repeat: no-repeat;
  position: relative;
  isolation: isolate;
  color: var(--flashhead-ink);

  *::-webkit-scrollbar {
    display: none;
  }
}

.wrap-electron {
  height: 100vh;
  overflow: hidden;
}

:global(.brand-badge) {
  width: clamp(180px, 17vw, 280px) !important;
  min-height: 44px !important;
  border-color: rgba(176, 31, 46, 0.16) !important;
  background: rgba(255, 255, 255, 0.9) !important;
  box-shadow: 0 14px 34px rgba(15, 23, 42, 0.08) !important;
}

:global(.page-container) {
  padding: clamp(18px, 2.4vw, 32px) !important;
}

:global(.video-container) {
  filter: drop-shadow(0 24px 48px rgba(15, 23, 42, 0.1)) !important;
}

:global(.video-container .local-video-container),
:global(.video-container .remote-video-container) {
  border: 1px solid rgba(148, 163, 184, 0.22) !important;
  border-radius: 28px !important;
  background: #fff !important;
}

:global(.video-container .local-video-container.scaled) {
  border-radius: 18px !important;
  box-shadow: 0 18px 36px rgba(15, 23, 42, 0.14) !important;
}

:global(.chat-input-container .chat-input-inner) {
  border-color: rgba(176, 31, 46, 0.14) !important;
  box-shadow: 0 18px 40px rgba(15, 23, 42, 0.09) !important;
}

:global(.chat-input-container .chat-input-inner .send-btn),
:global(.chat-input-container .stop-chat-btn),
:global(.answer-message-container.avatar) {
  background: var(--flashhead-red) !important;
}

@media (max-width: 720px) {
  :global(.brand-badge) {
    top: 10px !important;
    left: 12px !important;
    width: min(42vw, 180px) !important;
    min-height: 36px !important;
    padding: 6px 8px !important;
  }

  :global(.page-container) {
    padding: 16px 14px 20px !important;
    justify-content: flex-start !important;
  }

  :global(.video-container) {
    height: 82% !important;
    max-width: calc(100% - 78px) !important;
    margin: 0 64px 0 0 !important;
  }

  :global(.video-container .local-video-container),
  :global(.video-container .remote-video-container) {
    border-radius: 24px !important;
  }

  :global(.actions) {
    left: calc(100% + 10px) !important;
  }
}
</style>
