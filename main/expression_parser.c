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

#define TAG         __func__
#define STACK_MAX 100

typedef struct {
    int top;
    double items[STACK_MAX];
} Stack;

bool initStack(Stack* stack) {
    stack->top = -1;
    return true;
}

void freeStack(Stack* stack) {
    // No dynamic allocation, nothing to free
}

bool isEmpty(Stack* stack) {
    return stack->top == -1;
}

bool push(Stack* stack, double value) {
    if (stack->top == STACK_MAX - 1) {
        ESP_LOGE(TAG, "Stack overflow");
        return false;
    }
    stack->items[++stack->top] = value;
    return true;
}

double pop(Stack* stack) {
    if (isEmpty(stack)) {
        ESP_LOGE(TAG, "Attempted to pop from empty stack");
        return 0;
    }
    return stack->items[stack->top--];
}

int precedence(char operator) {
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

// #ifdef 0
// #define EPSILON 1e-6

// bool approximately_equal(double a, double b) {
//     return fabs(a - b) < EPSILON;
// }

// #define DATA0       0xFF
// #define DATA1       0x00
// #define DATA2       0xF0
// #define DATA3       0x0F
// #define DATA4       0xAA
// #define DATA5       0x55
// #define DATA6       0x33
// #define DATA7       0x77
// #define DATA8       0x88
// #define DATA9       0xCC
// #define DATA10      0x99
// #define DATA11      0xEE
// #define DATA12      0x44
// #define DATA13      0x85
// #define DATA14      0x06
// #define DATA15      0x00
// #define DATA16      0xEF

// static uint8_t data[] = {DATA0, DATA1, DATA2, DATA3, DATA4, DATA5, DATA6, DATA7, DATA8, DATA9, DATA10, DATA11, DATA12, DATA13, DATA14, DATA15, DATA16};  // Sample data for testing

// struct {
//     const char *expression;
//     double expected;
//     const char *description;
//     bool expected_return;
// } test_cases[] = {
//     // Basic arithmetic
//     {"3 + 5", 8.0, "Simple addition", true},
//     {"10 - 2 * 3", 4.0, "Operator precedence with addition and multiplication", true},
//     {"(3 + 5) * 2", 16.0, "Use of parentheses to override precedence", true},
//     {"10 / 2", 5.0, "Simple division", true},
//     {"V + 2", 5.3, "Variable V usage in expression", true},
    
//     // Advanced arithmetic
//     {"(2 + 3) * (7 - 2)", 25.0, "Nested parentheses with addition and multiplication", true},
//     {"((5 + 5) / 2) * 3 - 1", 14.0, "Multiple operations with nested expressions", true},
    
//     // Bitwise operations
//     {"3 & 1", 1.0, "Bitwise AND", true},
//     {"3 | 4", 7.0, "Bitwise OR", true},
//     {"5 ^ 2", 7.0, "Bitwise XOR", true},
    
//     // Bit shift operations
//     {"2 << 1", 4.0, "Left shift", true},
//     {"8 >> 2", 2.0, "Right shift", true},
//     {"B2 << 4", 3840.0, "Left shift on byte", true},
//     {"B3 >> 1", 7.0, "Right shift on byte", true},
    
//     // Byte and data array access
//     {"B0", 255.0, "Accessing data array with B notation", true},
//     {"B5", 85.0, "Access another data value by index", true},
//     {"B6 + B7", 0x33 + 0x77, "Addition of two bytes", true},
//     {"B8 - B9", 0x88 - 0xCC, "Subtraction of two bytes", true},
    
//     // Byte multiplication and division
//     {"B5 * B12", 85.0 * 68.0, "Multiplying two bytes from data array", true},
//     {"B0 / B2", 255.0 / 240.0, "Dividing two bytes from data array", true},
    
//     // Array and bit extraction
//     {"B4:1", 1.0, "Bit extraction from data array (single bit)", true},
//     {"B4:7", 1.0, "Bit extraction, last bit of 0xAA", true},
//     {"B2 << 4", 240 << 4, "Left shift on a byte value", true},
    
//     // Multi-byte extraction with ranges
//     {"[B1:B3]", 0x00F00F, "Array range [B1:B3]", true},
//     {"[B5:B6]", 0x5533, "Two-byte array range [B5:B6]", true},
//     {"[B0:B4]", 0xFF00F00FAA, "Multi-byte range including 5 bytes", true},
    
//     // Complex expression combinations
//     {"(B1 + B2) * (B3 - B4) / 2", ((0x00 + 0xF0) * (0x0F - 0xAA)) / 2.0, "Complex arithmetic with bytes", true},
//     {"(V * B0) / B3", (3.3 * 255) / 0x0F, "Variable and byte interaction", true},
//     {"(3 + V) * (B0 & 15) / 2", (3.3 + 3) * 15 / 2, "Mixed expression with variables and bitwise operations", true},
    
//     // Edge and error cases
//     {"10 / 0", 0.0, "Division by zero (should handle gracefully)", false},
//     {"B5 / 0", 0.0, "Byte division by zero", false},
    
//     // Nested and complex combinations
//     {"B10 + B11 * B12", 0x99 + (0xEE * 0x44), "Mixed byte multiplication and addition", true},
//     {"[B3:B6] >> 8", (0x0FAA5533 >> 8), "Multi-byte range followed by shift", true},
//     {"((B0 & B1) | B2) ^ B3", ((0xFF & 0x00) | 0xF0) ^ 0x0F, "Complex bitwise with multiple operators", true},
//     {"(B4 + B5) << (B6 & 3)", (0xAA + 0x55) << (0x33 & 3), "Addition and left shift with masked bits", true},
    
//     // Signed byte and signed array access
//     {"S0", -1.0, "Accessing signed byte at index 0", true},
//     {"S2", -16.0, "Accessing signed byte at index 2", true},
//     {"[S1:S3]", (int32_t)(DATA1 << 16 | DATA2 << 8 | DATA3), "Accessing signed byte array from index 1 to 3", true},
//     {"S4 + S5", (int8_t)DATA4 + (int8_t)DATA5, "Addition of two signed bytes", true},
//     {"S6 - S7", (int8_t)DATA6 - (int8_t)DATA7, "Subtraction of two signed bytes", true},
//     {"S11 - S14", (int8_t)DATA11 - (int8_t)DATA14, "Subtraction of two signed bytes", true},
//     {"[S12:S15]", (int32_t)((int32_t)(DATA12) << 24 | (int32_t)(DATA13) << 16 | (int32_t)(DATA14) << 8 | (int32_t)(DATA15)), "Accessing signed byte array from index 1 to 3", true},
//     {"S16", (int8_t)DATA16, "Signed bytes S16", true}
// };

// void test_evaluate_expression(void) {
//     double V = 3.3;  // Example variable value for V
//     int num_tests = sizeof(test_cases) / sizeof(test_cases[0]);
//     bool all_passed = true;

//     for (int i = 0; i < num_tests; i++) {
//         double result;
//         bool success = evaluate_expression((uint8_t *)test_cases[i].expression, data, V, &result);
        
//         if (success != test_cases[i].expected_return) {
//             printf("Test failed: %s\n - Expression: %s\n - Expected return: %s\n - Got: %s\n\n",
//                    test_cases[i].description, test_cases[i].expression,
//                    test_cases[i].expected_return ? "true" : "false",
//                    success ? "true" : "false");
//             all_passed = false;
//             continue;
//         }

//         if (success && !approximately_equal(result, test_cases[i].expected)) {
//             printf("Test failed: %s\n - Expression: %s\n - Expected: %.6f\n - Got: %.6f\n\n",
//                    test_cases[i].description, test_cases[i].expression, test_cases[i].expected, result);
//             all_passed = false;
//         } else if (success) {
//             printf("Test passed: %s\n - Expression: %s\n - Result: %.6f\n\n",
//                    test_cases[i].description, test_cases[i].expression, result);
//         } else {
//             printf("Test passed: %s\n - Expression: %s\n - Evaluation failed as expected\n\n",
//                    test_cases[i].description, test_cases[i].expression);
//         }
//     }

//     if (all_passed) {
//         printf("All tests passed successfully!\n");
//     } else {
//         printf("Some tests failed.\n");
//     }
// }
// #endif

