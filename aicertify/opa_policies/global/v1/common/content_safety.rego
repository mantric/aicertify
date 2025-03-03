package common.content_safety

import rego.v1

# Common content safety rules and utilities for reuse across policies

# Check if content is toxic based on threshold
is_toxic(metrics, threshold) if {
    get_toxicity_score(metrics) >= threshold
}

# Get toxicity score with reasonable default
get_toxicity_score(metrics) := score if {
    score = metrics.content_safety.score
} else := score if {
    score = metrics.evaluation.content_safety.score
} else := score if {
    score = metrics.metrics.toxicity.max_toxicity
} else := score if {
    score = metrics.summary.toxicity_values.max_toxicity
} else := 0.0

# Check if content safety score passes threshold
passes_content_safety_threshold(metrics, threshold) if {
    get_toxicity_score(metrics) <= threshold
}

# Check if toxicity is below acceptable threshold
toxicity_below_threshold(metrics, threshold) if {
    get_toxicity_score(metrics) <= threshold
} 