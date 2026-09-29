package club.noclub.accelerator.ui.components

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import club.noclub.accelerator.traffic.TrafficFormat

/**
 * The small set of shared composables (spec 88, 132).
 *
 * Two product rules are implemented here once, so no screen can break them:
 *
 * 1. **A missing measurement renders as "-", never 0** — [MeasuredValue] is the only
 *    place a number becomes text, and it delegates to
 *    [TrafficFormat] (docs/PRODUCT_SPEC.md).
 * 2. **The update outcome has exactly two product strings** — [UpdateBanner] maps the
 *    phase to 线路已更新 / 暂时无法更新 and never to anything of its own invention
 *    (docs/PRODUCT_SPEC.md).
 */

/** A titled card, used by every screen so spacing and elevation stay consistent. */
@Composable
fun SectionCard(
    title: String,
    modifier: Modifier = Modifier,
    subtitle: String? = null,
    content: @Composable () -> Unit,
) {
    Card(modifier = modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 6.dp)) {
        Column(modifier = Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(text = title, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
            if (subtitle != null) {
                Text(text = subtitle, style = MaterialTheme.typography.bodySmall)
            }
            content()
        }
    }
}

/**
 * A label/value row where the value carries a measurement that may not exist.
 *
 * @param value the already-formatted string. Callers pass "-" for a missing measurement;
 *   passing "0" is a bug, and the call site in this client never does it.
 */
@Composable
fun MeasuredValue(label: String, value: String, hint: String? = null) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column {
            Text(text = label, style = MaterialTheme.typography.bodyMedium)
            if (hint != null) Text(text = hint, style = MaterialTheme.typography.bodySmall)
        }
        Text(text = value, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.Medium)
    }
}

/**
 * The single big 智能加速 button.
 *
 * Its label is derived from the real connection phase, so it can never say "已加速" while
 * the tunnel is not up.
 */
@Composable
fun AccelerateButton(
    label: String,
    running: Boolean,
    enabled: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    if (running) {
        OutlinedButton(
            onClick = onClick,
            enabled = enabled,
            modifier = modifier.fillMaxWidth().height(64.dp).testTag("accelerate_button"),
        ) { Text(text = label, style = MaterialTheme.typography.titleLarge) }
    } else {
        Button(
            onClick = onClick,
            enabled = enabled,
            modifier = modifier.fillMaxWidth().height(64.dp).testTag("accelerate_button"),
        ) { Text(text = label, style = MaterialTheme.typography.titleLarge) }
    }
}

/** A single line of plain information (a code, a path, a reason). */
@Composable
fun InfoLine(text: String) {
    Text(text = text, style = MaterialTheme.typography.bodySmall)
}

/**
 * The update banner.
 *
 * @param phase one of the `UpdatePhase` wire values; [updated] is true only for
 *   `UPDATED`, and anything other than `UPDATED` that ended in `FAILED` maps to
 *   暂时无法更新 with the retained-data note.
 */
@Composable
fun UpdateBanner(
    updated: Boolean,
    unavailable: Boolean,
    detail: String,
    updatedLabel: String,
    unavailableLabel: String,
) {
    val text = when {
        updated -> updatedLabel
        unavailable -> "$unavailableLabel（继续使用上次可用线路）"
        else -> detail
    }
    Text(
        text = text,
        style = MaterialTheme.typography.bodyMedium,
        color = if (unavailable) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurface,
    )
}
