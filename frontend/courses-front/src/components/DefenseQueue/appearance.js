import { colors, sizes } from "../../../theme";

// Значения основной темы: очередь использует ту же палитру и форму кнопок.
export const queueAppearance = {
  "--queue-text": colors.textPrimary,
  "--queue-muted": colors.textSecondary,
  "--queue-accent": colors.buttonBackground,
  "--queue-border": colors.buttonBorder,
  "--queue-hover": colors.buttonHover,
  "--queue-success": colors.save,
  "--queue-warning": colors.edit,
  "--queue-danger": colors.error,
  "--queue-info": colors.cancel,
  "--queue-button-radius": "var(--q-radius-sm)",
  "--queue-font-size": sizes.fontSizeMedium,
  "--queue-title-size": sizes.fontSizeLarge,
};
