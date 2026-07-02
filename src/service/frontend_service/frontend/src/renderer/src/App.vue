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
  background:
    linear-gradient(135deg, rgba(255, 255, 255, 0.58), rgba(247, 249, 252, 0.08)),
    url(@/assets/background.png);
  height: calc(max(80vh, 100%));
  background-size: 100% 100%;
  background-repeat: no-repeat;
  position: relative;
  isolation: isolate;
  *::-webkit-scrollbar {
    display: none;
  }
}

.wrap-electron {
  height: 100vh;
  overflow: hidden;
}
</style>
