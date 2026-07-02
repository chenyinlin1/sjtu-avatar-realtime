<template>
  <div class="brand-badge" aria-label="Shanghai Jiao Tong University">
    <img src="@/assets/sjtu-logo.png" alt="Shanghai Jiao Tong University" />
  </div>
</template>

<style lang="less" scoped>
.brand-badge {
  position: fixed;
  top: clamp(14px, 2.4vw, 24px);
  left: clamp(14px, 2.4vw, 24px);
  z-index: 300;
  display: flex;
  align-items: center;
  width: clamp(190px, 20vw, 330px);
  max-width: calc(100vw - 32px);
  min-height: 48px;
  padding: clamp(8px, 1.1vw, 12px) clamp(12px, 1.4vw, 18px);
  border: 1px solid rgba(255, 255, 255, 0.72);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.78);
  box-shadow: 0 14px 40px rgba(94, 33, 41, 0.13);
  backdrop-filter: blur(14px) saturate(130%);
  -webkit-backdrop-filter: blur(14px) saturate(130%);
  -webkit-app-region: no-drag;
  pointer-events: none;

  img {
    display: block;
    width: 100%;
    height: auto;
    object-fit: contain;
  }
}

@media (max-width: 640px) {
  .brand-badge {
    width: min(58vw, 230px);
    min-height: 40px;
    padding: 7px 10px;
  }
}
</style>
