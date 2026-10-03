import { definePreset } from '@primeuix/themes'
import Aura from '@primeuix/themes/aura'

// Primary palette built around the HERMES logo blue (#303880 = 500).
const GwasHub = definePreset(Aura, {
  semantic: {
    primary: {
      50: '#eef0f8', 100: '#d9ddef', 200: '#b5bce0', 300: '#8b95cc', 400: '#5d68b0',
      500: '#303880', 600: '#29306e', 700: '#22285b', 800: '#1c2149', 900: '#151938', 950: '#0d1024',
    },
  },
})

export default {
  preset: GwasHub,
  options: { darkModeSelector: '.app-dark' },
}
