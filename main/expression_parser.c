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

#define TAG 		__func__
#define MAX_STACK_SIZE 100

typedef struct 
{
    double items[MAX_STACK_SIZE];
    int top;
} Stack;

static void initialize(Stack *stack) 
{
    stack->top = -1;
}

static bool isEmpty(Stack *stack) 
{
    return stack->top == -1;
}

static void push(Stack *stack, double item) 
{
    if (stack->top == MAX_STACK_SIZE - 1) 
	{
        ESP_LOGE(TAG, "Stack overflow\n");
    } 
	else 
	{
        stack->items[++stack->top] = item;
    }
}

// Function to pop a double value from the stack
static double pop(Stack *stack) 
{
    if (isEmpty(stack)) 
	{
        ESP_LOGE(TAG, "Stack underflow\n");
        return 0.0; // Return a default value
    } 
	else 
	{
        return stack->items[stack->top--];
    }
}

bool evaluate_expression(uint8_t *expression,  uint8_t *data, double V, double *result) 
{
    Stack operandStack;
    Stack operatorStack;
    initialize(&operandStack);
    initialize(&operatorStack);

    uint8_t i = 0;
    while (expression[i] != '\0') 
	{
        if (isspace(expression[i])) 
		{
            i++;
            continue; // Skip whitespace
        }
        if (isdigit(expression[i]) || (expression[i] == '.' && isdigit(expression[i + 1]))) 
		{
            char numBuffer[20];
            int j = 0;
            while (isdigit(expression[i]) || expression[i] == '.') 
			{
                numBuffer[j++] = expression[i++];
            }
            numBuffer[j] = '\0';
            double num = atof(numBuffer);
            push(&operandStack, num);
        } 
		else if (expression[i] == 'V') 
		{
            push(&operandStack, V); // Substitute the value of V
            i++;
        } 
        else if (expression[i] == 'B') 
        {
            i++; // Move past 'B'
            char byteIndexBuffer[4] = {0}; // Buffer to hold the byte index number as a string
            int bufIdx = 0;

            // Collect the next one or two digits
            while (isdigit(expression[i]) && bufIdx < 3)
            {
                byteIndexBuffer[bufIdx++] = expression[i++];
            }

            int byteIndex = atoi(byteIndexBuffer); // Convert the collected string to an integer

            if (byteIndex >= 0 && byteIndex < 64) // Now valid byte indexes are 0 to 63
            {
                push(&operandStack, (double)data[byteIndex]);
                // i is already incremented in the loop
            }
            else
            {
                ESP_LOGE(TAG, "Invalid byte index\n");
                return false; // Return failure
            }
        }
		else if (expression[i] == '(') 
		{
            push(&operatorStack, expression[i]);
            i++;
        } 
		else if (expression[i] == ')') 
		{
            while (!isEmpty(&operatorStack) && operatorStack.items[operatorStack.top] != '(') 
			{
                char operator = pop(&operatorStack);
                double operand2 = pop(&operandStack);
                double operand1 = pop(&operandStack);

                if (operator == '+') 
				{
                    push(&operandStack, operand1 + operand2);
                } 
				else if (operator == '-') 
				{
                    push(&operandStack, operand1 - operand2);
                } 
				else if (operator == '*') 
				{
                    push(&operandStack, operand1 * operand2);
                } 
				else if (operator == '/') 
				{
                    if (operand2 == 0) 
					{
                        ESP_LOGE(TAG, "Division by zero\n");
                        return false; // Return failure
                    }
                    push(&operandStack, operand1 / operand2);
                }
            }
            // Pop '(' from the operator stack
            if (!isEmpty(&operatorStack) && operatorStack.items[operatorStack.top] == '(') 
			{
                pop(&operatorStack);
            } 
			else 
			{
                ESP_LOGE(TAG, "Mismatched parentheses\n");
                return false; // Return failure
            }
            i++;
        } 
		else if (expression[i] == '+' || expression[i] == '-' || expression[i] == '*' || expression[i] == '/') 
		{
            while (!isEmpty(&operatorStack) &&
                   (operatorStack.items[operatorStack.top] == '*' || operatorStack.items[operatorStack.top] == '/') &&
                   (expression[i] == '+' || expression[i] == '-')) 
			{
                char operator = pop(&operatorStack);
                double operand2 = pop(&operandStack);
                double operand1 = pop(&operandStack);

                if (operator == '+') 
				{
                    push(&operandStack, operand1 + operand2);
                } 
				else if (operator == '-') 
				{
                    push(&operandStack, operand1 - operand2);
                } 
				else if (operator == '*') 
				{
                    push(&operandStack, operand1 * operand2);
                } 
				else if (operator == '/') 
				{
                    if (operand2 == 0) 
					{
                        ESP_LOGE(TAG, "Division by zero\n");
                        return false; // Return failure
                    }
                    push(&operandStack, operand1 / operand2);
                }
            }
            push(&operatorStack, expression[i]);
            i++;
        } 
		else 
		{
            ESP_LOGE(TAG, "Invalid character");
            return false; // Return failure
        }
    }

    // Check for remaining '(' in operator stack
    while (!isEmpty(&operatorStack)) 
	{
        char operator = pop(&operatorStack);
        if (operator == '(') 
		{
            ESP_LOGE(TAG, "Mismatched parentheses\n");
            return false; // Return failure
        }
        double operand2 = pop(&operandStack);
        double operand1 = pop(&operandStack);

        if (operator == '+') 
		{
            push(&operandStack, operand1 + operand2);
        } 
		else if (operator == '-') 
		{
            push(&operandStack, operand1 - operand2);
        } 
		else if (operator == '*') 
		{
            push(&operandStack, operand1 * operand2);
        } 
		else if (operator == '/') 
		{
            if (operand2 == 0) 
			{
                ESP_LOGE(TAG, "Division by zero\n");
                return false; // Return failure
            }
            push(&operandStack, operand1 / operand2);
        }
    }

    if (isEmpty(&operandStack) || operandStack.top != 0) 
	{
        ESP_LOGE(TAG, "Invalid expression\n");
        return false; // Return failure
    }

    *result = operandStack.items[0];
    return true; // Return success
}

