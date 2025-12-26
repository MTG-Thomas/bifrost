/**
 * AI Model Pricing API service - fully type-safe with openapi-fetch
 * Uses auto-generated types from OpenAPI spec
 */

import { apiClient } from "@/lib/api-client";
import type { components } from "@/lib/v1";

// Auto-generated types from OpenAPI spec
export type AIModelPricing = components["schemas"]["AIModelPricingPublic"];
export type AIModelPricingListItem =
	components["schemas"]["AIModelPricingListItem"];
export type AIModelPricingCreate =
	components["schemas"]["AIModelPricingCreate"];
export type AIModelPricingUpdate =
	components["schemas"]["AIModelPricingUpdate"];
export type AIModelPricingListResponse =
	components["schemas"]["AIModelPricingListResponse"];

export async function listPricing(): Promise<AIModelPricingListResponse> {
	const { data, error } = await apiClient.GET("/api/settings/ai/pricing");

	if (error) {
		throw new Error(`Failed to list pricing: ${JSON.stringify(error)}`);
	}

	return data as AIModelPricingListResponse;
}

export async function createPricing(
	data: AIModelPricingCreate,
): Promise<AIModelPricing> {
	const { data: responseData, error } = await apiClient.POST(
		"/api/settings/ai/pricing",
		{
			body: data,
		},
	);

	if (error) {
		throw new Error(`Failed to create pricing: ${JSON.stringify(error)}`);
	}

	return responseData as AIModelPricing;
}

export async function updatePricing(
	id: number,
	data: AIModelPricingUpdate,
): Promise<AIModelPricing> {
	const { data: responseData, error } = await apiClient.PUT(
		"/api/settings/ai/pricing/{pricing_id}",
		{
			params: { path: { pricing_id: id } },
			body: data,
		},
	);

	if (error) {
		throw new Error(`Failed to update pricing: ${JSON.stringify(error)}`);
	}

	return responseData as AIModelPricing;
}

export async function deletePricing(id: number): Promise<void> {
	const { error } = await apiClient.DELETE(
		"/api/settings/ai/pricing/{pricing_id}",
		{
			params: { path: { pricing_id: id } },
		},
	);

	if (error) {
		throw new Error(`Failed to delete pricing: ${JSON.stringify(error)}`);
	}
}
