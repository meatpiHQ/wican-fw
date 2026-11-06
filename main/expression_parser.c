/*
 * This file is part of the WiCAN project.
 *
 * Copyright (C) 2022  Meatpi Electronics.
 * Written by Ali Slim <ali@meatpi.com>
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program.  If not, see <http://www.gnu.org/licenses/>.
 */

#include "esp_log.h"
#include <string.h>
#include "types.h"
#include <stdbool.h>
#include <ctype.h>
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <math.h>

#define TAG 		__func__
#define STACK_MAX 100

typedef struct {
    int top;
    double items[STACK_MAX];
} Stack;

static bool initStack(Stack* stack) {
    stack->top = -1;
    return true;
}

static void freeStack(Stack* stack) {
    // No dynamic allocation, nothing to free
}

static bool isEmpty(Stack* stack) {
    return stack->top == -1;
}

static bool push(Stack* stack, double value) {
    if (stack->top == STACK_MAX - 1) {
        ESP_LOGE(TAG, "Stack overflow");
        return false;
    }
    stack->items[++stack->top] = value;
    return true;
}

static double pop(Stack* stack) {
    if (isEmpty(stack)) {
        ESP_LOGE(TAG, "Attempted to pop from empty stack");
        return 0;
    }
    return stack->items[stack->top--];
}

static int precedence(char operator) {
    if (operator == '|' || operator == '^') return 1;
    if (operator == '&') return 2;
    if (operator == '<' || operator == '>') return 3;  // For << and >>
    if (operator == '+' || operator == '-') return 4;
    if (operator == '*' || operator == '/') return 5;
    return 0;
}

static bool apply_operator(char op, Stack *operandStack) {
    if (operandStack->top < 1) {
        ESP_LOGE(TAG, "Operand stack underflow");
        return false;
    }
    double operand2 = pop(operandStack);
    double operand1 = pop(operandStack);
    double res = 0;
    switch (op) {
        case '+': res = operand1 + operand2; break;
        case '-': res = operand1 - operand2; break;
        case '*': res = operand1 * operand2; break;
        case '/':
            if (operand2 == 0) {
                ESP_LOGE(TAG, "Division by zero");
                return false;
            }
            res = operand1 / operand2;
            break;
        case '&': res = (double)((int64_t)operand1 & (int64_t)operand2); break;
        case '|': res = (double)((int64_t)operand1 | (int64_t)operand2); break;
        case '^': res = (double)((int64_t)operand1 ^ (int64_t)operand2); break;
        case '<': {
            int64_t shift = (int64_t)operand2;
            if (shift < 0 || shift >= 64) {
                ESP_LOGE(TAG, "Invalid shift amount: %lld", (long long)shift);
                return false;
            }
            res = (double)(int64_t)((uint64_t)(int64_t)operand1 << shift);
            break;
        }
        case '>': {
            int64_t shift = (int64_t)operand2;
            if (shift < 0 || shift >= 64) {
                ESP_LOGE(TAG, "Invalid shift amount: %lld", (long long)shift);
                return false;
            }
            res = (double)((int64_t)operand1 >> shift);
            break;
        }
        default:
            return false;
    }
    return push(operandStack, res);
}

// True if every byte in [start, end] lies inside a data_len-byte buffer.
// An inverted range reads nothing, so it is in bounds.
static bool byte_range_in_bounds(int start, int end, size_t data_len)
{
    if (start > end) {
        return true;
    }
    return start >= 0 && (size_t)end < data_len;
}

bool evaluate_expression(uint8_t *expression, uint8_t *data, size_t data_len, double V, double *result) {
    Stack operandStack, operatorStack;
    initStack(&operandStack);
    initStack(&operatorStack);

    int i = 0;
    while (expression[i] != '\0') {
        if (isspace((unsigned char)expression[i])) {
            i++;
            continue;
        }

        if (isdigit((unsigned char)expression[i]) || expression[i] == '.') {
            double value = strtod((char *)&expression[i], NULL);
            push(&operandStack, value);
            while (isdigit((unsigned char)expression[i]) || expression[i] == '.') {
                i++;
            }
        } else if (expression[i] == 'V') {
            push(&operandStack, V);
            i++;
        } else if (expression[i] == '[') {
            int start_index = 0, end_index = 0;
            int chars_read = 0;
            uint64_t sum_64 = 0;
            int result = sscanf((char *)expression + i, "[B%d:B%d]%n", &start_index, &end_index, &chars_read);
            if (result == 2 && chars_read > 0) {
                i += chars_read;
                if (end_index - start_index > 7) {
                    ESP_LOGE(TAG, "Range too large for 64-bit storage.");
                    freeStack(&operandStack);
                    freeStack(&operatorStack);
                    return false;
                }
                if (!byte_range_in_bounds(start_index, end_index, data_len)) {
                    ESP_LOGW(TAG, "[B%d:B%d] is past the end of a %u-byte response", start_index, end_index, (unsigned)data_len);
                    freeStack(&operandStack);
                    freeStack(&operatorStack);
                    return false;
                }
                for (int j = start_index; j <= end_index; j++) {
                    int shift_amount = (end_index - j) * 8;
                    sum_64 |= ((uint64_t)data[j] << shift_amount);
                }
                push(&operandStack, (double)sum_64);
            } else {
                chars_read = 0;
                result = sscanf((char *)expression + i, "[S%d:S%d]%n", &start_index, &end_index, &chars_read);
                if (result == 2 && chars_read > 0) {
                    i += chars_read;
                    if (end_index - start_index > 7) {
                        ESP_LOGE(TAG, "Range too large for 64-bit storage.");
                        freeStack(&operandStack);
                        freeStack(&operatorStack);
                        return false;
                    }
                    if (!byte_range_in_bounds(start_index, end_index, data_len)) {
                        ESP_LOGW(TAG, "[S%d:S%d] is past the end of a %u-byte response", start_index, end_index, (unsigned)data_len);
                        freeStack(&operandStack);
                        freeStack(&operatorStack);
                        return false;
                    }

                    uint64_t raw = 0;
                    for (int j = start_index; j <= end_index; j++) {
                        int shift_amount = (end_index - j) * 8;
                        raw |= ((uint64_t)data[j] << shift_amount);
                    }

                    int num_bytes = end_index - start_index + 1;
                    if (num_bytes > 0 && num_bytes < 8) {
                        uint64_t sign_bit = 1ULL << (num_bytes * 8 - 1);
                        if (raw & sign_bit) {
                            raw |= ~((1ULL << (num_bytes * 8)) - 1);
                        }
                    }
                    push(&operandStack, (double)(int64_t)raw);
                } else {
                    ESP_LOGE(TAG, "Invalid array syntax, couldn't parse indices correctly.");
                    freeStack(&operandStack);
                    freeStack(&operatorStack);
                    return false;
                }
            }
        } else if (expression[i] == 'B') {
            i++;
            if (!isdigit((unsigned char)expression[i])) {
                ESP_LOGE(TAG, "Missing byte index after B");
                freeStack(&operandStack);
                freeStack(&operatorStack);
                return false;
            }
            int index = 0;
            while (isdigit((unsigned char)expression[i])) {
                index = index * 10 + (expression[i] - '0');
                i++;
            }
            if ((size_t)index >= data_len) {
                ESP_LOGW(TAG, "B%d is past the end of a %u-byte response", index, (unsigned)data_len);
                freeStack(&operandStack);
                freeStack(&operatorStack);
                return false;
            }
            uint8_t value = data[index];
            if (expression[i] == ':') {
                i++;
                if (!isdigit((unsigned char)expression[i]) || (expression[i] - '0') > 7) {
                    ESP_LOGE(TAG, "Invalid bit index in B%d", index);
                    freeStack(&operandStack);
                    freeStack(&operatorStack);
                    return false;
                }
                uint8_t bit = expression[i] - '0';
                value = (value >> bit) & 1;
                i++;
            }
            push(&operandStack, value);
        } else if (expression[i] == 'S') {
            i++;
            if (!isdigit((unsigned char)expression[i])) {
                ESP_LOGE(TAG, "Missing byte index after S");
                freeStack(&operandStack);
                freeStack(&operatorStack);
                return false;
            }
            int index = 0;
            while (isdigit((unsigned char)expression[i])) {
                index = index * 10 + (expression[i] - '0');
                i++;
            }
            if ((size_t)index >= data_len) {
                ESP_LOGW(TAG, "S%d is past the end of a %u-byte response", index, (unsigned)data_len);
                freeStack(&operandStack);
                freeStack(&operatorStack);
                return false;
            }
            int8_t value = (int8_t)data[index];
            push(&operandStack, value);
        } else if (expression[i] == '(') {
            push(&operatorStack, expression[i]);
            i++;
        } else if (expression[i] == ')') {
            while (!isEmpty(&operatorStack) && operatorStack.items[operatorStack.top] != '(') {
                char operator = pop(&operatorStack);
                if (!apply_operator(operator, &operandStack)) {
                    freeStack(&operandStack);
                    freeStack(&operatorStack);
                    return false;
                }
            }
            if (!isEmpty(&operatorStack) && operatorStack.items[operatorStack.top] == '(') {
                pop(&operatorStack);
            } else {
                ESP_LOGE(TAG, "Mismatched parentheses");
                freeStack(&operandStack);
                freeStack(&operatorStack);
                return false;
            }
            i++;
        } else if (expression[i] == '+' || expression[i] == '-' || expression[i] == '*' || expression[i] == '/' || 
                   expression[i] == '&' || expression[i] == '|' || expression[i] == '^' ||
                   (expression[i] == '<' && expression[i+1] == '<') || (expression[i] == '>' && expression[i+1] == '>')) {
            char operator = expression[i];
            if ((operator == '<' || operator == '>') && expression[i+1] == operator) {
                i++; // Recognize << and >> as a single operator
            }
            while (!isEmpty(&operatorStack) && precedence(operatorStack.items[operatorStack.top]) >= precedence(operator)) {
                char op = pop(&operatorStack);
                if (!apply_operator(op, &operandStack)) {
                    freeStack(&operandStack);
                    freeStack(&operatorStack);
                    return false;
                }
            }
            push(&operatorStack, operator);
            i++;
        } else {
            ESP_LOGE(TAG, "Invalid character: %c", expression[i]);
            freeStack(&operandStack);
            freeStack(&operatorStack);
            return false;
        }
    }

    // Final evaluation of remaining operators in the operator stack
    while (!isEmpty(&operatorStack)) {
        char operator = pop(&operatorStack);
        if (!apply_operator(operator, &operandStack)) {
            freeStack(&operandStack);
            freeStack(&operatorStack);
            return false;
        }
    }

    // Ensure there's exactly one result in the operand stack
    if (isEmpty(&operandStack) || operandStack.top != 0) {
        ESP_LOGE(TAG, "Invalid expression");
        freeStack(&operandStack);
        freeStack(&operatorStack);
        return false;
    }

    *result = operandStack.items[0];
    freeStack(&operandStack);
    freeStack(&operatorStack);
    return true;
}

