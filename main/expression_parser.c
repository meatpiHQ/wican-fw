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

bool evaluate_expression(uint8_t *expression, uint8_t *data, double V, double *result) {
    Stack operandStack, operatorStack;
    initStack(&operandStack);
    initStack(&operatorStack);

    int i = 0;
    while (expression[i] != '\0') {
        if (isspace(expression[i])) {
            i++;
            continue;
        }

        if (isdigit(expression[i]) || expression[i] == '.') {
            double value = strtod((char *)&expression[i], NULL);
            push(&operandStack, value);
            while (isdigit(expression[i]) || expression[i] == '.') {
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
            if (result == 2) {
                i += chars_read;
                if (end_index - start_index > 7) {
                    ESP_LOGE(TAG, "Range too large for 64-bit storage.");
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
                result = sscanf((char *)expression + i, "[S%d:S%d]%n", &start_index, &end_index, &chars_read);
                if (result == 2) {
                    i += chars_read;
                    if (end_index - start_index > 7) {
                        ESP_LOGE(TAG, "Range too large for 64-bit storage.");
                        freeStack(&operandStack);
                        freeStack(&operatorStack);
                        return false;
                    }

                    int64_t sum_64_signed = 0;
                    int32_t sum_32_signed = 0;
                    int16_t sum_16_signed = 0;
                    int8_t sum_8_signed = 0;

                    for (int j = start_index; j <= end_index; j++) {
                        int shift_amount = (end_index - j) * 8;
                        sum_64_signed |= (data[j] << shift_amount);
                        
                        if (end_index - start_index <= 3) {
                            sum_32_signed |= (data[j] << shift_amount);
                        }
                        if (end_index - start_index <= 1) {
                            sum_16_signed |= (data[j] << shift_amount);
                        }
                        if (end_index - start_index == 0) {
                            sum_8_signed = data[j];
                        }
                    }

                    if (end_index - start_index == 0) {
                        printf("sum_8_signed\r\n");
                        push(&operandStack, (double)sum_8_signed);
                    } else if (end_index - start_index == 1) {
                        printf("sum_16_signed\r\n");
                        push(&operandStack, (double)sum_16_signed);
                    } else if (end_index - start_index <= 3) {
                        printf("sum_32_signed: %ld\r\n", sum_32_signed);
                        push(&operandStack, (double)sum_32_signed);
                    } else {
                        printf("sum_64_signed\r\n");
                        push(&operandStack, (double)sum_64_signed);
                    }
                } else {
                    ESP_LOGE(TAG, "Invalid array syntax, couldn't parse indices correctly.");
                    freeStack(&operandStack);
                    freeStack(&operatorStack);
                    return false;
                }
            }
        } else if (expression[i] == 'B') {
            i++;
            int index = 0;
            while (isdigit(expression[i])) {
                index = index * 10 + (expression[i] - '0');
                i++;
            }
            uint8_t value = data[index];
            if (expression[i] == ':') {
                i++;
                uint8_t bit = expression[i] - '0';
                value = (value >> bit) & 1;
                i++;
            }
            push(&operandStack, value);
        } else if (expression[i] == 'S') {
            i++;
            int index = 0;
            while (isdigit(expression[i])) {
                index = index * 10 + (expression[i] - '0');
                i++;
            }
            int8_t value = (int8_t)data[index];
            push(&operandStack, value);
        } else if (expression[i] == '(') {
            push(&operatorStack, expression[i]);
            i++;
        } else if (expression[i] == ')') {
            while (!isEmpty(&operatorStack) && operatorStack.items[operatorStack.top] != '(') {
                char operator = pop(&operatorStack);
                if (isEmpty(&operandStack)) {
                    ESP_LOGE(TAG, "Operand stack underflow");
                    freeStack(&operandStack);
                    freeStack(&operatorStack);
                    return false;
                }
                double operand2 = pop(&operandStack);
                double operand1 = pop(&operandStack);
                double result = 0;
                switch (operator) {
                    case '+': result = operand1 + operand2; break;
                    case '-': result = operand1 - operand2; break;
                    case '*': result = operand1 * operand2; break;
                    case '/': 
                        if (operand2 == 0) {
                            ESP_LOGE(TAG, "Division by zero");
                            freeStack(&operandStack);
                            freeStack(&operatorStack);
                            return false;
                        }
                        result = operand1 / operand2;
                        break;
                    case '&': result = (int)operand1 & (int)operand2; break;
                    case '|': result = (int)operand1 | (int)operand2; break;
                    case '^': result = (int)operand1 ^ (int)operand2; break;
                    case '<': result = (int)operand1 << (int)operand2; break;
                    case '>': result = (int)operand1 >> (int)operand2; break;
                }
                push(&operandStack, result);
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
                if (isEmpty(&operandStack)) {
                    ESP_LOGE(TAG, "Operand stack underflow");
                    freeStack(&operandStack);
                    freeStack(&operatorStack);
                    return false;
                }
                double operand2 = pop(&operandStack);
                double operand1 = pop(&operandStack);
                double result = 0;
                switch (op) {
                    case '+': result = operand1 + operand2; break;
                    case '-': result = operand1 - operand2; break;
                    case '*': result = operand1 * operand2; break;
                    case '/': 
                        if (operand2 == 0) {
                            ESP_LOGE(TAG, "Division by zero");
                            freeStack(&operandStack);
                            freeStack(&operatorStack);
                            return false;
                        }
                        result = operand1 / operand2;
                        break;
                    case '&': result = (int)operand1 & (int)operand2; break;
                    case '|': result = (int)operand1 | (int)operand2; break;
                    case '^': result = (int)operand1 ^ (int)operand2; break;
                    case '<': result = (int)operand1 << (int)operand2; break;
                    case '>': result = (int)operand1 >> (int)operand2; break;
                }
                push(&operandStack, result);
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
        if (isEmpty(&operandStack)) {
            ESP_LOGE(TAG, "Operand stack underflow");
            freeStack(&operandStack);
            freeStack(&operatorStack);
            return false;
        }
        double operand2 = pop(&operandStack);
        double operand1 = pop(&operandStack);
        double result = 0;
        switch (operator) {
            case '+': result = operand1 + operand2; break;
            case '-': result = operand1 - operand2; break;
            case '*': result = operand1 * operand2; break;
            case '/':
                if (operand2 == 0) {
                    ESP_LOGE(TAG, "Division by zero");
                    freeStack(&operandStack);
                    freeStack(&operatorStack);
                    return false;
                }
                result = operand1 / operand2;
                break;
            case '&': result = (int)operand1 & (int)operand2; break;
            case '|': result = (int)operand1 | (int)operand2; break;
            case '^': result = (int)operand1 ^ (int)operand2; break;
            case '<': result = (int)operand1 << (int)operand2; break;
            case '>': result = (int)operand1 >> (int)operand2; break;
        }
        push(&operandStack, result);
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

