# ---------- Notifications: subscribe Lambda -> SNS topic -> email ----------

# One topic for every subscriber. Each email subscription has a filter policy
# {"species": [...]} (set by lambdas/subscribe), and the notify Lambda publishes
# one message per file with a `species` message attribute listing its new tags,
# so SNS only emails users who chose at least one of them.
# The display name is the sender name on the confirmation and notification
# emails (instead of "AWS Notifications").
resource "aws_sns_topic" "notifications" {
  name         = "aussie-ecoguard-notifications"
  display_name = "Aussie EcoGuard"
}
