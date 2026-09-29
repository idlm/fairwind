package club.noclub.accelerator.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

/**
 * The app's Material3 theme (spec 88, 132).
 *
 * A small, hand-picked palette rather than a generated one: the product has a single
 * accent (the 智能加速 button) and everything else steps back. No game artwork, no
 * brand asset, no third-party palette is used.
 */
private val LightScheme = lightColorScheme(
    primary = Color(0xFF0EA5E9),
    onPrimary = Color.White,
    secondary = Color(0xFF334155),
    background = Color(0xFFF8FAFC),
    surface = Color.White,
    error = Color(0xFFDC2626),
)

private val DarkScheme = darkColorScheme(
    primary = Color(0xFF38BDF8),
    onPrimary = Color(0xFF0B1220),
    secondary = Color(0xFF94A3B8),
    background = Color(0xFF0B1220),
    surface = Color(0xFF111C2E),
    error = Color(0xFFF87171),
)

/** Wraps the app in the Material3 theme, following the system light/dark setting. */
@Composable
fun AcceleratorTheme(
    darkTheme: Boolean = isSystemInDarkTheme(),
    content: @Composable () -> Unit,
) {
    MaterialTheme(colorScheme = if (darkTheme) DarkScheme else LightScheme, content = content)
}
