package global.v1.common.fairness

import rego.v1

# Common fairness rules and utilities for reuse across policies

# Check gender bias in fairness metrics
gender_bias_detected(metrics) if {
	metrics.fairness.gender_bias_detected == true
}

gender_bias_detected(metrics) if {
	metrics.gender_bias_detected == true
}

gender_bias_detected(metrics) if {
	metrics.summary.stereotype_values.gender_bias_detected == true
}

gender_bias_detected(_) := false

# Check racial bias in fairness metrics
racial_bias_detected(metrics) if {
	metrics.fairness.racial_bias_detected == true
}

racial_bias_detected(metrics) if {
	metrics.racial_bias_detected == true
}

racial_bias_detected(metrics) if {
	metrics.summary.stereotype_values.racial_bias_detected == true
}

racial_bias_detected(_) := false

# Get fairness score with reasonable default
fairness_score(metrics) := score if {
	score = metrics.fairness.score
} else := score if {
	score = metrics.evaluation.fairness.score
} else := 0.0

# Check if fairness score passes threshold
passes_fairness_threshold(metrics, threshold) if {
	fairness_score(metrics) >= threshold
}
