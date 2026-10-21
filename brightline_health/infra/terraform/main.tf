# SKELETON (not applied/validated here): Azure footprint for the V1 stack on deck slide 5.
terraform {
  required_providers { azurerm = { source = "hashicorp/azurerm", version = "~> 3.100" } }
}
provider "azurerm" { features {} }

variable "location" { default = "uksouth" }
variable "prefix"   { default = "blhr-health" }

resource "azurerm_resource_group" "rg" { name = "${var.prefix}-rg" location = var.location }

resource "azurerm_eventhub_namespace" "ns" {
  name = "${var.prefix}-ehns" location = var.location resource_group_name = azurerm_resource_group.rg.name
  sku = "Standard" capacity = 1
}
resource "azurerm_eventhub" "logs" {
  name = "request-logs" namespace_name = azurerm_eventhub_namespace.ns.name
  resource_group_name = azurerm_resource_group.rg.name partition_count = 4 message_retention = 1
}

resource "azurerm_kusto_cluster" "adx" {
  name = "blhradx" location = var.location resource_group_name = azurerm_resource_group.rg.name
  sku { name = "Dev(No SLA)_Standard_E2a_v4" capacity = 1 }   # switch to Standard_D11_v2 x2 for prod SLA
}
resource "azurerm_kusto_database" "db" {
  name = "health" resource_group_name = azurerm_resource_group.rg.name cluster_name = azurerm_kusto_cluster.adx.name
  hot_cache_period = "P7D" soft_delete_period = "P60D"            # 60-day raw retention
}

resource "azurerm_key_vault" "kv" {
  name = "${var.prefix}-kv" location = var.location resource_group_name = azurerm_resource_group.rg.name
  tenant_id = data.azurerm_client_config.cur.tenant_id sku_name = "standard"
}
data "azurerm_client_config" "cur" {}

resource "azurerm_log_analytics_workspace" "law" {
  name = "${var.prefix}-law" location = var.location resource_group_name = azurerm_resource_group.rg.name sku = "PerGB2018"
}
resource "azurerm_container_app_environment" "env" {
  name = "${var.prefix}-cae" location = var.location resource_group_name = azurerm_resource_group.rg.name
  log_analytics_workspace_id = azurerm_log_analytics_workspace.law.id
}
resource "azurerm_container_app" "api" {
  name = "${var.prefix}-api" container_app_environment_id = azurerm_container_app_environment.env.id
  resource_group_name = azurerm_resource_group.rg.name revision_mode = "Single"
  template {
    min_replicas = 1 max_replicas = 3
    container { name = "api" image = "REPLACE_ACR/blhr-health:latest" cpu = 0.5 memory = "1Gi" }
  }
  ingress { external_enabled = true target_port = 8000 traffic_weight { latest_revision = true percentage = 100 } }
}
# Function App (mask & validate), Managed Grafana, Entra app roles, Power Automate flow: add in V1 build.
